from __future__ import annotations

import json
import logging
import uuid
from pathlib import Path
from typing import Any, Dict, Iterable, List

from VoidCube_core.utils import atomic_json_write
from systems.runtime_task_profile import derive_runtime_task_profile

from .models import (
    ExperimentRecord,
    LearningConclusion,
    LearningRecommendation,
    LearningSession,
    LearningTopic,
    SupervisorConclusionSubmission,
    SupervisorTaskProposal,
)


logger = logging.getLogger(__name__)


class SelfLearningConclusionStore:
    """Persistence store for learning conclusions and supervisor payloads.

    This class persists historical learning artifacts and builds Supervisor
    submissions. It is not an autonomous execution service; API-A autonomous
    execution of `self_learning` tasks happens through the task pull path.
    """

    def __init__(
        self,
        storage_root: str | Path,
        *,
        governance_repository: Any | None = None,
    ) -> None:
        self.storage_root = Path(storage_root).resolve()
        self.governance_repository = governance_repository
        self.topics_root = self.storage_root / "topics"
        self.sessions_root = self.storage_root / "sessions"
        self.experiments_root = self.storage_root / "experiments"
        self.conclusions_root = self.storage_root / "conclusions"
        for root in (
            self.topics_root,
            self.sessions_root,
            self.experiments_root,
            self.conclusions_root,
        ):
            root.mkdir(parents=True, exist_ok=True)

    def create_topic(
        self,
        *,
        title: str,
        reason: str,
        source: str = "self_learning",
        tags: Iterable[str] | None = None,
        metadata: Dict[str, Any] | None = None,
    ) -> LearningTopic:
        topic = LearningTopic(
            topic_id=str(uuid.uuid4()),
            title=title,
            reason=reason,
            source=source,
            tags=list(tags or []),
            metadata=dict(metadata or {}),
        )
        self._write_model(self.topics_root / f"{topic.topic_id}.json", topic)
        return topic

    def plan_session(
        self,
        *,
        topic: LearningTopic,
        planned_minutes: int = 30,
        trigger: str = "scheduled",
        notes: Iterable[str] | None = None,
    ) -> LearningSession:
        session = LearningSession(
            session_id=str(uuid.uuid4()),
            topic_id=topic.topic_id,
            planned_minutes=planned_minutes,
            trigger=trigger,
            notes=list(notes or []),
        )
        self._write_model(self.sessions_root / f"{session.session_id}.json", session)
        return session

    def record_experiment(
        self,
        *,
        topic: LearningTopic,
        session: LearningSession,
        hypothesis: str,
        method: str,
        observations: Iterable[str] | None = None,
        outcome: str = "pending",
        compared_against: Iterable[str] | None = None,
    ) -> ExperimentRecord:
        record = ExperimentRecord(
            experiment_id=str(uuid.uuid4()),
            topic_id=topic.topic_id,
            session_id=session.session_id,
            hypothesis=hypothesis,
            method=method,
            observations=list(observations or []),
            outcome=outcome,
            compared_against=list(compared_against or []),
        )
        self._write_model(self.experiments_root / f"{record.experiment_id}.json", record)
        return record

    def submit_conclusion(
        self,
        *,
        topic: LearningTopic,
        session: LearningSession,
        experiments: Iterable[ExperimentRecord],
        comparisons: Iterable[str],
        summary: str,
        verified: bool,
        recommendations: Iterable[LearningRecommendation] | None = None,
    ) -> LearningConclusion:
        conclusion = LearningConclusion(
            conclusion_id=str(uuid.uuid4()),
            topic=topic,
            session=session,
            experiments=list(experiments),
            comparisons=list(comparisons),
            summary=summary,
            verified=verified,
            recommendations=list(recommendations or []),
        )
        # The injected repository is the only local governance-event writer.
        if self.governance_repository is not None:
            from memai.governance import GovernanceEvent, GovernanceEventType, GovernanceDecision
            self.governance_repository.append(GovernanceEvent.create(
                event_type=GovernanceEventType.CANDIDATE_REVIEW,
                source_actor="self_learning",
                decision=GovernanceDecision.RECORD_ONLY,
                reason=f"Self-learning conclusion: {summary[:120]}",
                task_id=conclusion.conclusion_id,
                execution_result={
                    "title": conclusion.topic.title,
                    "summary": conclusion.summary,
                    "task_type": "self_learning",
                    "runtime_task_profile": {
                        "governance_task_type": "self_learning",
                        "task_family": "self_learning",
                        "execution_kind": None,
                    },
                    "constraints": {},
                    "topic_id": conclusion.topic.topic_id,
                    "session_id": conclusion.session.session_id,
                    "verified": conclusion.verified,
                    "recommendations_count": len(conclusion.recommendations),
                },
            ))
        self._write_model(
            self.conclusions_root / f"{conclusion.conclusion_id}.json",
            conclusion,
        )

        return conclusion

    def record_feedback(self, conclusion_id: str, *, useful: bool) -> Dict[str, Any]:
        """Record post-hoc usefulness feedback for a conclusion (SL-03).

        Stores a simple counter so future iterations can weight conclusion
        quality when generating new learning plans.
        """
        feedback_path = self.storage_root / "feedback.json"
        data: Dict[str, Any] = {}
        if feedback_path.exists():
            try:
                data = json.loads(feedback_path.read_text(encoding="utf-8"))
            except Exception:
                data = {}
        entry = data.setdefault(conclusion_id, {"useful": 0, "not_useful": 0, "total": 0})
        if useful:
            entry["useful"] += 1
        else:
            entry["not_useful"] += 1
        entry["total"] = entry["useful"] + entry["not_useful"]
        feedback_path.write_text(json.dumps(data, indent=2, ensure_ascii=False))
        return entry

    def build_supervisor_submission(
        self,
        conclusion: LearningConclusion,
    ) -> SupervisorConclusionSubmission:
        proposals = self._build_task_proposals(conclusion)
        return SupervisorConclusionSubmission(
            conclusion_id=conclusion.conclusion_id,
            topic_id=conclusion.topic.topic_id,
            title=conclusion.topic.title,
            summary=conclusion.summary,
            verified=conclusion.verified,
            recommendations=list(conclusion.recommendations),
            evidence={
                "comparisons": list(conclusion.comparisons),
                "experiments": [
                    experiment.model_dump(mode="json") for experiment in conclusion.experiments
                ],
            },
            metadata={
                "session_id": conclusion.session.session_id,
                "topic_reason": conclusion.topic.reason,
                "topic_tags": list(conclusion.topic.tags),
            },
            proposals=proposals,
        )

    def build_supervisor_payload(self, conclusion: LearningConclusion) -> Dict[str, Any]:
        payload = self.build_supervisor_submission(conclusion).model_dump(mode="json")
        for proposal in payload.get("proposals") or []:
            if proposal.get("task_type") is None:
                proposal.pop("task_type", None)
        return payload

    def _build_task_proposals(
        self,
        conclusion: LearningConclusion,
    ) -> List[SupervisorTaskProposal]:
        proposals: List[SupervisorTaskProposal] = []
        base_metadata = {
            "topic_id": conclusion.topic.topic_id,
            "conclusion_id": conclusion.conclusion_id,
            "session_id": conclusion.session.session_id,
            "verified": conclusion.verified,
        }
        base_evidence = {
            "summary": conclusion.summary,
            "comparisons": list(conclusion.comparisons),
            "experiment_ids": [experiment.experiment_id for experiment in conclusion.experiments],
        }

        for recommendation in conclusion.recommendations:
            if recommendation.recommendation_type not in {
                "propose_evolution_task",
                "propose_experiment",
            }:
                continue

            task_type = (
                "self_evolution"
                if recommendation.recommendation_type == "propose_evolution_task"
                else "self_learning_followup"
            )
            runtime_task_profile = derive_runtime_task_profile(
                task_type=task_type,
                default_task_family="general_self_evolution",
            )
            proposals.append(
                SupervisorTaskProposal(
                    title=recommendation.title,
                    summary=recommendation.summary or conclusion.summary,
                    governance_task_type=runtime_task_profile["governance_task_type"],
                    task_family=runtime_task_profile["task_family"],
                    execution_kind=runtime_task_profile["execution_kind"],
                    source="self_learning",
                    metadata={
                        **base_metadata,
                        "recommendation_type": recommendation.recommendation_type,
                        "governance_task_type": runtime_task_profile["governance_task_type"],
                        "task_family": runtime_task_profile["task_family"],
                        **(
                            {"execution_kind": runtime_task_profile["execution_kind"]}
                            if runtime_task_profile["execution_kind"] is not None
                            else {}
                        ),
                    },
                    evidence={
                        **base_evidence,
                        **dict(recommendation.evidence),
                    },
                    constraints=dict(recommendation.constraints),
                )
            )

        return proposals

    def _write_model(self, path: Path, model: Any) -> None:
        atomic_json_write(path, model.model_dump(mode="json"))

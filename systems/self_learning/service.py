from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any, Dict, Iterable, List

from VoidCube_core.utils import atomic_json_write
from systems.runtime_task_profile import derive_runtime_task_profile, resolve_broad_task_type

from .models import (
    ExperimentRecord,
    LearningConclusion,
    LearningRecommendation,
    LearningSession,
    LearningTopic,
    SupervisorConclusionSubmission,
    SupervisorTaskProposal,
)


class SelfLearningService:
    """Minimal learn-only service that never executes upgrades or switches."""

    def __init__(self, storage_root: str | Path) -> None:
        self.storage_root = Path(storage_root).resolve()
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
        self._write_model(self.conclusions_root / f"{conclusion.conclusion_id}.json", conclusion)
        # Best-effort writeback to MemAI governance repository (SL-01).
        # Failure here must not block the conclusion save or the supervisor
        # submission — Mem is the long-term soul layer, but the local file
        # is the authoritative store for now.
        try:
            from memai.governance import GovernanceEvent, GovernanceEventType, GovernanceDecision
            from memai.governance_repository import GovernanceEventRepository
            repo_path = self.storage_root / "mem_governance.jsonl"
            repo = GovernanceEventRepository(str(repo_path))
            repo.append(GovernanceEvent.create(
                event_type=GovernanceEventType.CANDIDATE_REVIEW,
                source_actor="self_learning",
                decision=GovernanceDecision.RECORD_ONLY,
                reason=f"Self-learning conclusion: {summary[:120]}",
                task_id=conclusion.conclusion_id,
            ))
        except Exception:
            pass
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

    def submit_to_supervisor_queue(
        self,
        *,
        conclusion: LearningConclusion,
        queue: Any,
    ) -> List[Dict[str, Any]]:
        submission = self.build_supervisor_submission(conclusion)
        created = []
        for proposal in submission.proposals:
            task = queue.create_task(
                title=proposal.title,
                summary=proposal.summary,
                task_type=self._resolved_proposal_task_type(proposal),
                source=proposal.source,
                priority=proposal.priority,
                metadata=self._proposal_task_metadata(proposal),
                evidence=proposal.evidence,
                constraints=proposal.constraints,
            )
            created.append(self._serialize_task_payload(task))
        return created

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

    def _proposal_task_metadata(self, proposal: SupervisorTaskProposal) -> Dict[str, Any]:
        metadata = dict(proposal.metadata)
        if proposal.governance_task_type is not None:
            metadata.setdefault("governance_task_type", proposal.governance_task_type)
        if proposal.task_family is not None:
            metadata.setdefault("task_family", proposal.task_family)
        if proposal.execution_kind is not None:
            metadata.setdefault("execution_kind", proposal.execution_kind)
        return metadata

    def _resolved_proposal_task_type(self, proposal: SupervisorTaskProposal) -> str:
        return resolve_broad_task_type(
            task_type=proposal.task_type,
            governance_task_type=proposal.governance_task_type,
            task_family=proposal.task_family,
            execution_kind=proposal.execution_kind,
            source=proposal.source,
        )

    def _serialize_task_payload(self, task: Any) -> Dict[str, Any]:
        payload = task.model_dump(mode="json")
        metadata = dict(payload.get("metadata") or {})
        runtime_task_profile = derive_runtime_task_profile(
            task_type=payload.get("task_type"),
            governance_task_type=(
                payload.get("governance_task_type")
                or metadata.get("governance_task_type")
            ),
            task_family=payload.get("task_family") or metadata.get("task_family"),
            execution_kind=(
                payload.get("execution_kind")
                or metadata.get("execution_kind")
            ),
            default_task_family="general_self_evolution",
        )
        payload["governance_task_type"] = runtime_task_profile["governance_task_type"]
        payload["task_family"] = runtime_task_profile["task_family"]
        payload["execution_kind"] = runtime_task_profile["execution_kind"]
        return payload

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional


CORE_VALUES: Dict[str, str] = {
    "continuity": "Preserve VoidCube's long-term memory, lineage, and service continuity.",
    "truthfulness": "Surface uncertainty, correction signals, and evidence gaps before they harden.",
    "creativity": "Turn idle capacity into bounded learning and improvement proposals.",
}


@dataclass(frozen=True, slots=True)
class EndogenousTaskCandidate:
    stable_key: str
    title: str
    summary: str
    priority: str
    governance_task_type: str
    task_family: str
    execution_kind: Optional[str]
    value_tags: List[str]
    utility: float
    evidence: Dict[str, Any] = field(default_factory=dict)
    constraints: Dict[str, Any] = field(default_factory=dict)

    def to_queue_item(self) -> Dict[str, Any]:
        metadata: Dict[str, Any] = {
            "source": "endogenous_drive",
            "endogenous_drive_key": self.stable_key,
            "core_values": list(self.value_tags),
            "utility": self.utility,
            "governance_task_type": self.governance_task_type,
            "task_family": self.task_family,
        }
        if self.execution_kind is not None:
            metadata["execution_kind"] = self.execution_kind
        return {
            "title": self.title,
            "summary": self.summary,
            "source": "endogenous_drive",
            "priority": self.priority,
            "governance_task_type": self.governance_task_type,
            "task_family": self.task_family,
            "execution_kind": self.execution_kind,
            "metadata": metadata,
            "evidence": {
                "endogenous_drive": {
                    "stable_key": self.stable_key,
                    "core_values": list(self.value_tags),
                    "core_value_definitions": {
                        key: CORE_VALUES[key] for key in self.value_tags if key in CORE_VALUES
                    },
                    "utility": self.utility,
                },
                **dict(self.evidence),
            },
            "constraints": dict(self.constraints),
        }


class EndogenousDriveEngine:
    """Deterministic supervisor drive loop.

    The drive engine does not execute work. It turns system facts and core values
    into auditable queue candidates that still pass through supervisor review.
    """

    def generate_candidates(
        self,
        *,
        idle_window: Dict[str, Any],
        existing_drive_keys: Iterable[str],
        max_candidates: int = 3,
    ) -> List[EndogenousTaskCandidate]:
        existing_keys = set(existing_drive_keys)
        candidates = [
            candidate
            for candidate in self._candidate_stream(idle_window)
            if candidate.stable_key not in existing_keys
        ]
        candidates.sort(key=lambda candidate: candidate.utility, reverse=True)
        return candidates[:max(max_candidates, 0)]

    def _candidate_stream(self, idle_window: Dict[str, Any]) -> List[EndogenousTaskCandidate]:
        activity = dict(idle_window.get("activity") or {})
        counts = dict(activity.get("counts") or {})
        decisions_by_family = dict(idle_window.get("task_family_decisions") or {})
        decisions_by_governance = dict(idle_window.get("governance_task_type_decisions") or {})

        memory_plan = self._decision_for(
            "memory_maintenance",
            decisions_by_family,
            decisions_by_governance,
        )
        self_learning_plan = self._decision_for(
            "self_learning",
            decisions_by_family,
            decisions_by_governance,
        )
        self_evolution_plan = self._decision_for(
            "general_self_evolution",
            decisions_by_family,
            decisions_by_governance,
        )

        candidates: List[EndogenousTaskCandidate] = []
        if memory_plan.get("eligible_for_planning"):
            candidates.append(
                EndogenousTaskCandidate(
                    stable_key="continuity:memory_maintenance_sweep",
                    title="Maintain long-term memory continuity",
                    summary=(
                        "Inspect memory-maintenance needs during an idle window so long-term "
                        "identity, summaries, and governance traces stay usable."
                    ),
                    priority="high",
                    governance_task_type="memory_maintenance",
                    task_family="memory_maintenance",
                    execution_kind="memory_maintenance",
                    value_tags=["continuity"],
                    utility=0.92,
                    evidence={
                        "idle_window_checks": dict(idle_window.get("checks") or {}),
                        "idle_seconds": dict(idle_window.get("idle_seconds") or {}),
                    },
                )
            )

        recent_errors = int(counts.get("error_count") or counts.get("recent_errors") or 0)
        uncertainty_count = int(
            counts.get("uncertainty_high_count")
            or counts.get("high_uncertainty")
            or 0
        )
        correction_signals = recent_errors + uncertainty_count
        if correction_signals > 0 and self_learning_plan.get("eligible_for_planning"):
            candidates.append(
                EndogenousTaskCandidate(
                    stable_key="truthfulness:review_correction_signals",
                    title="Review recent uncertainty and correction signals",
                    summary=(
                        "Turn recent errors or high-uncertainty answers into a bounded "
                        "self-learning follow-up instead of letting them remain invisible."
                    ),
                    priority="high" if correction_signals >= 3 else "normal",
                    governance_task_type="self_learning",
                    task_family="self_learning",
                    execution_kind=None,
                    value_tags=["truthfulness"],
                    utility=min(0.65 + correction_signals * 0.08, 0.95),
                    evidence={
                        "recent_errors": recent_errors,
                        "uncertainty_high_count": uncertainty_count,
                    },
                )
            )

        active_sessions = int(activity.get("active_sessions") or 0)
        if active_sessions == 0 and self_learning_plan.get("eligible_for_planning"):
            # Extract a real topic from recent gateway activity metadata
            learning_topic = self._extract_learning_topic(activity)
            title = (
                f"Research: {learning_topic}"
                if learning_topic
                else "Explore one unresolved learning thread"
            )
            summary = (
                f"Use idle capacity to research '{learning_topic}' — the most recent "
                f"user-discussed topic that may benefit from deeper investigation."
                if learning_topic
                else "Use idle capacity to ask the agent for one evidence-producing "
                     "learning pass over unresolved recent topics."
            )
            utility = 0.68 if learning_topic else 0.58
            candidates.append(
                EndogenousTaskCandidate(
                    stable_key="creativity:idle_learning_thread",
                    title=title,
                    summary=summary,
                    priority="normal",
                    governance_task_type="self_learning",
                    task_family="self_learning",
                    execution_kind=None,
                    value_tags=["creativity"],
                    utility=utility,
                    evidence={
                        "active_sessions": active_sessions,
                        "trigger": "idle_capacity",
                        "learning_topic": learning_topic or "",
                    },
                    constraints={
                        "execution_policy": "learn_only",
                        "must_not_modify_active_body": True,
                    },
                )
            )

        if self_evolution_plan.get("eligible_for_planning"):
            candidates.append(
                EndogenousTaskCandidate(
                    stable_key="continuity:queue_hygiene_review",
                    title="Review self-evolution queue hygiene",
                    summary=(
                        "Check whether planned, deferred, or paused self-evolution work still "
                        "has enough evidence and clear rollback constraints."
                    ),
                    priority="normal",
                    governance_task_type="self_evolution",
                    task_family="general_self_evolution",
                    execution_kind="general_self_evolution",
                    value_tags=["continuity", "truthfulness"],
                    utility=0.52,
                    evidence={
                        "trigger": "supervisor_queue_governance",
                    },
                    constraints={
                        "must_not_execute_without_review": True,
                    },
                )
            )

        return candidates

    def _extract_learning_topic(self, activity: Dict[str, Any]) -> str:
        """Extract a concise learning topic from recent gateway activity metadata.

        Looks at the most recent user_request and agent_work metadata to find
        topics that were discussed but may benefit from deeper research.
        Returns empty string if no meaningful topic can be extracted.
        """
        recent = dict(activity.get("recent_metadata") or {})
        user_req = recent.get("user_request") or {}
        agent_work = recent.get("agent_work") or {}

        # Try to extract a topic from the user's last request
        user_text = str(user_req.get("text") or user_req.get("query") or "")
        if not user_text:
            user_text = str(user_req.get("summary") or "")
        if user_text and len(user_text) > 10:
            # Take first sentence or first 80 chars as the topic
            topic = user_text.split(".")[0].split("\n")[0].strip()
            if len(topic) > 80:
                topic = topic[:77] + "..."
            if len(topic) >= 10:
                return topic

        # Fall back to agent's last response summary
        agent_text = str(agent_work.get("summary") or agent_work.get("title") or "")
        if agent_text and len(agent_text) > 10:
            topic = agent_text.split(".")[0].strip()
            if len(topic) > 80:
                topic = topic[:77] + "..."
            if len(topic) >= 10:
                return topic

        return ""

    def _decision_for(
        self,
        family: str,
        decisions_by_family: Dict[str, Any],
        decisions_by_governance: Dict[str, Any],
    ) -> Dict[str, Any]:
        if family in decisions_by_family:
            return dict(decisions_by_family[family] or {})
        governance = "self_evolution"
        if family in {"memory_maintenance", "self_learning", "user"}:
            governance = family
        return dict(decisions_by_governance.get(governance) or {})

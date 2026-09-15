"""Explicit autonomous task state machine and transition evidence."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping


AUTONOMOUS_TASK_TRANSITIONS: dict[str, frozenset[str]] = {
    "planned": frozenset({"approved", "paused", "cancelled", "deferred", "awaiting_review"}),
    "awaiting_review": frozenset({"approved", "planned", "deferred", "paused", "cancelled"}),
    "approved": frozenset({"running", "cancelled", "deferred", "paused"}),
    "running": frozenset({
        "reconciling", "awaiting_user_consent", "awaiting_review",
        "completed", "failed", "paused", "retry",
    }),
    "reconciling": frozenset({"approved", "completed", "failed", "paused"}),
    "awaiting_user_consent": frozenset({"completed", "failed", "cancelled"}),
    "paused": frozenset({"planned", "approved", "cancelled", "deferred"}),
    "deferred": frozenset({"planned", "approved", "cancelled", "paused", "awaiting_review"}),
    "retry": frozenset({"approved", "planned", "deferred", "paused", "cancelled"}),
    "completed": frozenset(),
    "failed": frozenset(),
    "cancelled": frozenset(),
}


@dataclass(frozen=True, slots=True)
class AutonomousTaskTransition:
    """Auditable state transition metadata shared across persistence layers."""

    task_id: str
    from_status: str
    to_status: str
    reason: str = ""
    cycle_id: str = ""
    lease_id: str = ""
    attempt: int = 0
    evidence_refs: tuple[str, ...] = ()
    memory_write_status: str = "unknown"
    context: Mapping[str, Any] = field(default_factory=dict)


def validate_autonomous_task_transition(current: str, target: str) -> None:
    """Raise ``ValueError`` unless ``current → target`` is legal."""
    current = str(current or "").strip().lower()
    target = str(target or "").strip().lower()
    if current not in AUTONOMOUS_TASK_TRANSITIONS:
        raise ValueError(f"Unknown task state: {current}")
    if target not in AUTONOMOUS_TASK_TRANSITIONS:
        raise ValueError(f"Unknown task target state: {target}")
    if current != target and target not in AUTONOMOUS_TASK_TRANSITIONS[current]:
        legal = AUTONOMOUS_TASK_TRANSITIONS[current]
        raise ValueError(
            f"Illegal task state transition: {current} → {target} "
            f"(legal: {', '.join(sorted(legal)) if legal else 'terminal'})"
        )


def validate_task_outcome_bundle(
    target_status: str,
    *,
    execution_outcome_status: str = "unknown",
    memory_write_status: str = "unknown",
) -> None:
    """Reject terminal task outcomes that contradict their side effects.

    Unknown values remain valid for legacy records; explicit failure may never
    be represented as a completed task. Memory state is intentionally not
    required for execution completion because writes may be asynchronous.
    """
    target = str(target_status or "").strip().lower()
    execution = str(execution_outcome_status or "unknown").strip().lower()
    memory = str(memory_write_status or "unknown").strip().lower()
    allowed_execution = {"unknown", "succeeded", "queued", "skipped", "failed", "degraded"}
    allowed_memory = {"unknown", "succeeded", "queued", "skipped", "failed", "degraded"}
    if execution not in allowed_execution:
        raise ValueError(f"Unknown execution outcome status: {execution}")
    if memory not in allowed_memory:
        raise ValueError(f"Unknown memory write status: {memory}")
    if target == "completed" and execution in {"failed", "degraded"}:
        raise ValueError(
            "Completed autonomous task requires a successful execution outcome; "
            f"got {execution}"
        )


__all__ = [
    "AUTONOMOUS_TASK_TRANSITIONS",
    "AutonomousTaskTransition",
    "validate_autonomous_task_transition",
    "validate_task_outcome_bundle",
]

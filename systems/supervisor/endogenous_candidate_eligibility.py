"""Pure eligibility projections for the deterministic endogenous candidate stream."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Mapping, Optional

from systems.supervisor.endogenous_candidate_pipeline import (
    active_api_b_judgement_candidate_kinds,
)

STATIC_GOVERNANCE_CANDIDATE_COOLDOWN_HOURS = 12
MEMORY_MAINTENANCE_STABLE_KEY = "continuity:memory_maintenance_sweep"
GOVERNANCE_HYGIENE_STABLE_KEY = "continuity:governance_hygiene_review"


@dataclass(frozen=True, slots=True)
class CandidateStreamEligibility:
    """Resolved gates consumed by the deterministic candidate stream."""

    active_candidate_kinds: frozenset[str]
    memory_maintenance: bool
    truthfulness_review: bool
    shell_baseline_learning: bool
    exploratory_learning: bool
    governance_hygiene_review: bool
    body_improvement: bool
    governance_signal_present: bool


def resolve_candidate_stream_eligibility(
    *,
    api_b_judgement_tasks: Iterable[dict[str, Any]],
    existing_keys: Iterable[str],
    memory_planning_eligible: bool,
    self_learning_planning_eligible: bool,
    autonomous_improvement_planning_eligible: bool,
    truthfulness_signal_present: bool,
    shell_slot_id: str,
    shell_worktree: str,
    has_learning_history: bool,
    governance_signal_present: bool,
    body_projection_available: bool,
    body_growth_blocked: bool,
    body_growth_quota: int,
    memory_maintenance_status: Mapping[str, Any] | None = None,
    now: Optional[datetime] = None,
) -> CandidateStreamEligibility:
    task_list = list(api_b_judgement_tasks)
    active = frozenset(active_api_b_judgement_candidate_kinds(task_list))
    existing = {str(key or "").strip() for key in existing_keys}
    memory_status = dict(memory_maintenance_status or {})
    memory_run_status = str(memory_status.get("status") or "").strip().lower()
    memory_status_reliable = False
    memory_due = True
    if memory_run_status in {"accepted", "running", "in_progress"}:
        memory_status_reliable = True
        memory_due = False
    elif memory_run_status in {"idle", "completed"} and "maintenance_due" in memory_status:
        memory_status_reliable = True
        memory_due = bool(memory_status.get("maintenance_due"))
    elif memory_run_status in {"failed", "cancelled"}:
        memory_status_reliable = True
        memory_due = True
    memory_available = (
        memory_planning_eligible
        and "memory_maintenance" not in active
        and MEMORY_MAINTENANCE_STABLE_KEY not in existing
        and (
            (memory_due if memory_status_reliable else True)
            and (
                memory_status_reliable
                or not has_recent_static_governance_completion(
                    task_list,
                    stable_key=MEMORY_MAINTENANCE_STABLE_KEY,
                    now=now,
                )
            )
        )
    )
    truthfulness_available = (
        truthfulness_signal_present
        and self_learning_planning_eligible
        and "truthfulness_review" not in active
        and "truthfulness:review_correction_signals" not in existing
    )
    shell_baseline_available = False
    if self_learning_planning_eligible:
        normalized_slot_id = str(shell_slot_id or "shell").strip() or "shell"
        shell_baseline_key = (
            f"creativity:self_learning:shell_baseline:{normalized_slot_id}"
        )
        shell_baseline_available = (
            bool(str(shell_worktree or "").strip())
            and not has_learning_history
            and "shell_baseline_learning" not in active
            and shell_baseline_key not in existing
        )
    governance_available = (
        autonomous_improvement_planning_eligible
        and "governance_hygiene_review" not in active
        and GOVERNANCE_HYGIENE_STABLE_KEY not in existing
        and not has_recent_static_governance_completion(
            task_list,
            stable_key=GOVERNANCE_HYGIENE_STABLE_KEY,
            now=now,
        )
        and governance_signal_present
    )
    return CandidateStreamEligibility(
        active_candidate_kinds=active,
        memory_maintenance=memory_available,
        truthfulness_review=truthfulness_available,
        shell_baseline_learning=shell_baseline_available,
        exploratory_learning=(
            self_learning_planning_eligible
            and "exploratory_learning" not in active
        ),
        governance_hygiene_review=governance_available,
        body_improvement=(
            autonomous_improvement_planning_eligible
            and "body_improvement" not in active
            and body_projection_available
            and not body_growth_blocked
            and body_growth_quota > 0
        ),
        governance_signal_present=governance_signal_present,
    )


def has_recent_static_governance_completion(
    tasks: Iterable[dict[str, Any]],
    *,
    stable_key: str,
    now: Optional[datetime] = None,
    cooldown_hours: int = STATIC_GOVERNANCE_CANDIDATE_COOLDOWN_HOURS,
) -> bool:
    key = str(stable_key or "").strip()
    if not key or cooldown_hours <= 0:
        return False
    current_time = now or datetime.now(timezone.utc)
    for task in tasks:
        if not isinstance(task, dict):
            continue
        if str(task.get("status") or "").strip().lower() != "completed":
            continue
        metadata = dict(task.get("metadata") or {})
        evidence = dict(task.get("evidence") or {})
        task_key = str(
            metadata.get("endogenous_drive_key")
            or evidence.get("endogenous_drive_key")
            or ""
        ).strip()
        if task_key != key:
            continue
        completed_at = (
            metadata.get("completed_at")
            or task.get("updated_at")
            or task.get("created_at")
        )
        completed_time = parse_timestamp(completed_at)
        if completed_time is not None and current_time - completed_time <= timedelta(
            hours=cooldown_hours
        ):
            return True
    return False


def parse_timestamp(raw_timestamp: Any) -> Optional[datetime]:
    if not raw_timestamp:
        return None
    try:
        parsed = datetime.fromisoformat(str(raw_timestamp))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed

"""Pure readiness and cooldown gates for body improvement candidates."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, Optional


BODY_IMPROVEMENT_ACTIVE_STATUSES = {
    "planned",
    "approved",
    "running",
    "awaiting_user_consent",
    "paused",
    "deferred",
    "awaiting_review",
    "retry",
}


def calculate_learning_quality_score(
    completed_learning_tasks: Iterable[Dict[str, Any]],
    *,
    now: Optional[datetime] = None,
) -> float:
    try:
        learning_tasks = [
            task for task in completed_learning_tasks if isinstance(task, dict)
        ]
        completed_count = len(learning_tasks)
        if completed_count == 0:
            return 0.0

        quality_sum = 0.0
        freshness_sum = 0.0
        current_time = now or datetime.now(timezone.utc)
        for task in learning_tasks:
            raw_quality = task.get("quality_score")
            try:
                quality = float(raw_quality)
            except (TypeError, ValueError):
                # Completion alone is not evidence that a learning result is useful.
                # Missing quality must keep body improvement below its hard gate.
                return 0.0
            if not 0.0 <= quality <= 1.0:
                return 0.0
            quality_sum += quality
            completed_at = task.get("completed_at")
            if completed_at:
                try:
                    parsed = datetime.fromisoformat(str(completed_at))
                    if parsed.tzinfo is None:
                        parsed = parsed.replace(tzinfo=timezone.utc)
                    age_days = (current_time - parsed).days
                    freshness_sum += max(0.0, 1.0 - age_days / 90.0)
                except (TypeError, ValueError, OverflowError):
                    freshness_sum += 0.5
            else:
                freshness_sum += 0.5

        avg_quality = quality_sum / completed_count
        avg_freshness = freshness_sum / completed_count
        score = avg_quality * 60 + avg_freshness * 40
        return max(0.0, min(100.0, score))
    except (AttributeError, TypeError, ValueError, ZeroDivisionError):
        return 0.0


def resolve_body_improvement_eligibility(
    *,
    completed_learning_tasks: Iterable[Dict[str, Any]],
    shell_slot_id: str,
    shell_worktree: str,
    policy: Dict[str, Any],
    api_b_judgement_tasks: Iterable[Dict[str, Any]],
    now: Optional[datetime] = None,
    body_readiness: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    learning_tasks = [
        dict(task) for task in completed_learning_tasks if isinstance(task, dict)
    ]
    slot_id = str(shell_slot_id or "").strip()
    worktree = str(shell_worktree or "").strip()
    if not slot_id or not worktree:
        return {"available": False, "reason": "shell_slot_unavailable"}
    if not learning_tasks:
        return {"available": False, "reason": "learning_evidence_unavailable"}

    learning_quality_score = calculate_learning_quality_score(
        learning_tasks,
        now=now,
    )
    min_quality = float(policy.get("body_improvement_min_quality") or 60.0)
    if learning_quality_score < min_quality:
        return {
            "available": False,
            "reason": "learning_quality_below_threshold",
            "learning_quality_score": round(learning_quality_score, 4),
            "required_learning_quality": round(min_quality, 4),
        }

    cooldown_hours = int(policy.get("body_improvement_cooldown_hours") or 12)
    if has_recent_body_improvement(
        api_b_judgement_tasks,
        shell_slot_id=slot_id,
        cooldown_hours=cooldown_hours,
        now=now,
    ):
        return {
            "available": False,
            "reason": "body_improvement_cooldown",
            "learning_quality_score": round(learning_quality_score, 4),
        }
    if body_readiness is not None and not body_readiness.get("ready"):
        return {
            "available": False,
            "reason": "body_baseline_unavailable",
            "body_readiness": dict(body_readiness),
            "learning_quality_score": round(learning_quality_score, 4),
        }
    return {
        "available": True,
        "learning_quality_score": round(learning_quality_score, 4),
        "completed_learning_tasks": learning_tasks,
        "shell_slot_id": slot_id,
        "shell_worktree": worktree,
        **({"body_readiness": dict(body_readiness)} if body_readiness is not None else {}),
    }


def has_recent_body_improvement(
    tasks: Iterable[Dict[str, Any]],
    *,
    shell_slot_id: str,
    cooldown_hours: int,
    now: Optional[datetime] = None,
) -> bool:
    slot_id = str(shell_slot_id or "").strip()
    current_time = now or datetime.now(timezone.utc)
    task_list = [task for task in tasks if isinstance(task, dict)]

    for task in task_list:
        if str(task.get("execution_kind") or "").strip().lower() != "body_improvement":
            continue
        status = str(task.get("status") or "").strip().lower()
        if status not in BODY_IMPROVEMENT_ACTIVE_STATUSES:
            continue
        target_slot_id = str(
            dict(task.get("constraints") or {}).get("target_slot_id") or ""
        ).strip()
        if not slot_id or not target_slot_id or slot_id == target_slot_id:
            return True

    for task in task_list:
        if str(task.get("execution_kind") or "").strip().lower() != "body_improvement":
            continue
        if str(task.get("status") or "").strip().lower() != "completed":
            continue
        target_slot_id = str(
            dict(task.get("constraints") or {}).get("target_slot_id") or ""
        ).strip()
        if slot_id and target_slot_id and slot_id != target_slot_id:
            continue
        completed_at = task.get("updated_at") or task.get("created_at")
        parsed = parse_timestamp(completed_at)
        if (
            cooldown_hours > 0
            and parsed is not None
            and current_time - parsed <= timedelta(hours=cooldown_hours)
        ):
            return True
    return False


def parse_timestamp(raw_timestamp: Any) -> Optional[datetime]:
    if not raw_timestamp:
        return None
    try:
        parsed = datetime.fromisoformat(str(raw_timestamp))
    except (TypeError, ValueError, OverflowError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed

"""Pure backlog pressure and urgency projections for endogenous candidates."""

from __future__ import annotations

from typing import Any, Dict, Optional


def backlog_pressure_penalty(
    drive_context: Dict[str, Any],
    *,
    governance_task_type: Optional[str] = None,
    task_family: Optional[str] = None,
    execution_kind: Optional[str] = None,
) -> float:
    total_active = int(drive_context.get("api_b_judgement_count") or 0)
    related = 0
    if governance_task_type:
        related += int(
            dict(drive_context.get("active_backlog_by_governance") or {}).get(
                governance_task_type,
                0,
            )
        )
    if task_family:
        related += int(
            dict(drive_context.get("active_backlog_by_family") or {}).get(
                task_family,
                0,
            )
        )
    if execution_kind:
        related += int(
            dict(drive_context.get("active_backlog_by_execution_kind") or {}).get(
                execution_kind,
                0,
            )
        )
    penalty = 0.02 * max(total_active - 1, 0) + 0.03 * related
    return round(min(penalty, 0.28), 4)


def build_backlog_pressure_penalties(drive_context: Dict[str, Any]) -> Dict[str, float]:
    return {
        "memory_maintenance": backlog_pressure_penalty(
            drive_context,
            governance_task_type="memory_maintenance",
            task_family="memory_maintenance",
            execution_kind="memory_maintenance",
        ),
        "self_learning": backlog_pressure_penalty(
            drive_context,
            governance_task_type="self_learning",
            task_family="self_learning",
        ),
        "body_improvement": backlog_pressure_penalty(
            drive_context,
            governance_task_type="self_evolution",
            task_family="body_upgrade",
            execution_kind="body_improvement",
        ),
    }


def memory_maintenance_urgency(drive_input: Dict[str, Any]) -> float:
    idle_seconds = dict(drive_input.get("idle_seconds") or {})
    coverage = [
        _clamp01(float(value or 0) / 900.0)
        for value in (
            idle_seconds.get("user"),
            idle_seconds.get("employee_execution"),
            idle_seconds.get("memory"),
        )
    ]
    avg_idle_coverage = sum(coverage) / len(coverage) if coverage else 0.0
    # Whole-day execution (baseline section 6): no time-of-day window bonus.
    return round(_clamp01(0.72 + avg_idle_coverage * 0.18), 4)


def governance_hygiene_urgency(drive_context: Dict[str, Any]) -> float:
    api_b_judgement_count = int(drive_context.get("api_b_judgement_count") or 0)
    stale_backlog_count = int(drive_context.get("stale_backlog_count") or 0)
    pending_review_count = int(drive_context.get("pending_review_count") or 0)
    urgency = (
        0.24
        + min(api_b_judgement_count, 5) * 0.08
        + min(stale_backlog_count + pending_review_count, 3) * 0.08
    )
    return round(_clamp01(urgency), 4)


def _clamp01(value: Any) -> float:
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return 0.0

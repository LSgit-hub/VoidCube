"""Pure perception and world-model projections for endogenous drive."""

from __future__ import annotations

from typing import Any, Dict, Optional, Protocol

from .endogenous_body_eligibility import (
    calculate_learning_quality_score,
)


def build_drive_perception_projection(
    *,
    drive_input: Dict[str, Any],
    activity: Dict[str, Any],
    drive_context: Dict[str, Any],
    counts: Dict[str, Any],
    correction_signals: int,
    shell_slot_meta: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    checks = dict(drive_input.get("checks") or {})
    idle_seconds = dict(drive_input.get("idle_seconds") or {})
    user_chain_signal = dict(drive_input.get("user_chain_signal") or {})
    autonomous_chain_gate_active = bool(drive_input.get("autonomous_chain_gate_active", False))
    active_sessions = int(activity.get("active_sessions") or 0)
    learning_backlog_count = len(list(drive_context.get("learning_backlog_titles") or []))
    body_improvement_backlog_count = len(
        list(drive_context.get("body_improvement_backlog_titles") or [])
    )
    stale_backlog_count = int(drive_context.get("stale_backlog_count") or 0)
    pending_review_count = int(drive_context.get("pending_review_count") or 0)
    api_b_judgement_count = int(drive_context.get("api_b_judgement_count") or 0)
    employee_dispatch_count = int(
        drive_context.get("employee_dispatch_count")
        if drive_context.get("employee_dispatch_count") is not None
        else 0
    )
    employee_running_count = int(drive_context.get("employee_running_count") or 0)
    learning_quality = calculate_learning_quality_score(
        list(drive_input.get("completed_learning_tasks") or [])
    )
    recent_errors = int(counts.get("error_count") or counts.get("recent_errors") or 0)
    uncertainty_count = int(
        counts.get("uncertainty_high_count") or counts.get("high_uncertainty") or 0
    )
    shell_slot_id = str((shell_slot_meta or {}).get("slot_id") or "").strip()
    shell_slot_present = bool(shell_slot_id or (shell_slot_meta or {}).get("worktree_path"))
    user_chain_quiet = bool(user_chain_signal.get("is_quiet", active_sessions <= 0))

    user_mode = "serving_user"
    if autonomous_chain_gate_active:
        user_mode = "autonomous_chain_gate"
    elif user_chain_quiet:
        user_mode = "user_chain_quiet"

    system_posture = "stable"
    if active_sessions > 0:
        system_posture = "serving_user"
    elif correction_signals >= 4:
        system_posture = "strained"
    elif pending_review_count > 0 or stale_backlog_count > 1:
        system_posture = "degrading"
    elif learning_quality >= 60.0 and shell_slot_present:
        system_posture = "growth_window"

    return {
        "user_mode": user_mode,
        "autonomous_chain_gate_active": autonomous_chain_gate_active,
        "system_posture": system_posture,
        "active_sessions": active_sessions,
        "recent_errors": recent_errors,
        "uncertainty_count": uncertainty_count,
        "correction_signals": max(0, correction_signals),
        "learning_quality": learning_quality,
        "has_learning_history": bool(drive_input.get("completed_learning_tasks") or []),
        "shell_slot_present": shell_slot_present,
        "shell_slot_id": shell_slot_id,
        "api_b_judgement_count": api_b_judgement_count,
        "learning_backlog_count": learning_backlog_count,
        "body_improvement_backlog_count": body_improvement_backlog_count,
        "stale_backlog_count": stale_backlog_count,
        "pending_review_count": pending_review_count,
        "employee_dispatch_count": employee_dispatch_count,
        "employee_running_count": employee_running_count,
        "checks": checks,
        "idle_seconds": idle_seconds,
    }


class PerceptionView(Protocol):
    user_mode: str
    system_posture: str
    correction_signals: int
    learning_quality: float
    has_learning_history: bool
    learning_backlog_count: int
    shell_slot_present: bool
    body_improvement_backlog_count: int
    api_b_judgement_count: int
    stale_backlog_count: int
    pending_review_count: int
    autonomous_chain_gate_active: bool
    active_sessions: int


def build_drive_world_model_projection(perception: PerceptionView) -> Dict[str, Any]:
    truthfulness_pressure = _clamp01(
        0.15 + min(perception.correction_signals, 6) / 6.0 * 0.75
    )
    learning_momentum = _clamp01(
        (perception.learning_quality / 100.0) * 0.8
        + (0.1 if perception.has_learning_history else 0.0)
        - min(perception.learning_backlog_count, 3) * 0.08
    )
    body_upgrade_readiness = _clamp01(
        (perception.learning_quality / 100.0) * 0.7
        + (0.15 if perception.shell_slot_present else 0.0)
        - min(perception.body_improvement_backlog_count, 2) * 0.2
    )
    backlog_strain = min(
        perception.api_b_judgement_count * 0.08
        + perception.stale_backlog_count * 0.12
        + perception.pending_review_count * 0.1,
        1.0,
    )
    memory_pressure = _clamp01(0.25 + min(perception.stale_backlog_count, 3) * 0.08)
    self_confidence = _clamp01(
        0.55
        + (0.08 if perception.autonomous_chain_gate_active else 0.0)
        - min(perception.active_sessions, 3) * 0.08
        - min(perception.pending_review_count, 3) * 0.04
    )
    governance_load_state = "clear"
    if backlog_strain >= 0.55:
        governance_load_state = "strained"
    elif backlog_strain >= 0.3:
        governance_load_state = "busy"

    return {
        "user_mode": perception.user_mode,
        "system_posture": perception.system_posture,
        "truthfulness_pressure": truthfulness_pressure,
        "learning_momentum": learning_momentum,
        "body_upgrade_readiness": body_upgrade_readiness,
        "governance_load_state": governance_load_state,
        "memory_pressure": memory_pressure,
        "self_confidence": self_confidence,
    }


def _clamp01(value: Any) -> float:
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return 0.0

"""Pure evaluation of Supervisor drive input from an activity snapshot."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Mapping

from .activity_projection import (
    idle_seconds_since,
    parse_activity_timestamp,
)


@dataclass(frozen=True, slots=True)
class DriveInputEvaluationConfig:
    """Runtime values required to evaluate one drive-input snapshot."""

    gateway_address: str
    now: datetime
    user_idle_seconds: int
    memory_idle_seconds: int
    workflow_idle_seconds: int
    perception_scope: str
    autonomous_chain_gate_active: bool
    evidence_packet: Mapping[str, Any]


def evaluate_drive_input_snapshot(
    *,
    request: Mapping[str, Any],
    snapshot: Mapping[str, Any],
    config: DriveInputEvaluationConfig,
    task_profile: Mapping[str, Any],
    shell_slot: Mapping[str, Any] | None,
    completed_learning_tasks: list[Mapping[str, Any]],
) -> dict[str, Any]:
    """Evaluate activity guards without accessing Supervisor state or services."""
    requested_governance_task_type = str(task_profile["governance_task_type"])
    requested_task_family = str(task_profile["task_family"])

    last_user_request_at = parse_activity_timestamp(snapshot.get("last_user_request_at"))
    last_memory_task_at = parse_activity_timestamp(snapshot.get("last_memory_task_at"))
    last_self_learning_activity_at = parse_activity_timestamp(
        snapshot.get("last_self_learning_activity_at")
    )
    last_autonomous_chain_plan_at = parse_activity_timestamp(
        snapshot.get("last_autonomous_chain_plan_at")
    )
    last_autonomous_chain_execute_at = parse_activity_timestamp(
        snapshot.get("last_autonomous_chain_execute_at")
    )
    last_autonomous_chain_activity_at = parse_activity_timestamp(
        snapshot.get("last_autonomous_chain_activity_at")
    )
    active_cli_executor = dict(snapshot.get("active_cli_executor") or {})
    active_cli_lane = str(active_cli_executor.get("agent_lane") or "").strip().lower()
    active_cli_lease_status = str(active_cli_executor.get("lease_status") or "").strip().lower()
    active_cli_execution_idle_seconds: float | None = None
    if active_cli_lane == "supervisor_task":
        try:
            active_cli_execution_idle_seconds = max(
                0.0,
                float(active_cli_executor.get("idle_seconds") or 0.0),
            )
        except (TypeError, ValueError):
            active_cli_execution_idle_seconds = 0.0
    active_cli_execution_is_stale = (
        not active_cli_executor
        or active_cli_lane != "supervisor_task"
        or bool(active_cli_executor.get("is_stale"))
        or active_cli_lease_status == "stale"
    )

    user_idle_seconds = idle_seconds_since(last_user_request_at, now=config.now)
    memory_idle_seconds = idle_seconds_since(last_memory_task_at, now=config.now)
    self_learning_idle_seconds = idle_seconds_since(
        last_self_learning_activity_at,
        now=config.now,
    )
    autonomous_chain_plan_idle_seconds = idle_seconds_since(
        last_autonomous_chain_plan_at,
        now=config.now,
    )
    autonomous_chain_execute_idle_seconds = idle_seconds_since(
        last_autonomous_chain_execute_at,
        now=config.now,
    )
    autonomous_chain_idle_seconds = idle_seconds_since(
        last_autonomous_chain_activity_at,
        now=config.now,
    )
    autonomous_execution_idle_candidates = [
        value
        for value in (
            autonomous_chain_execute_idle_seconds,
            active_cli_execution_idle_seconds,
        )
        if value is not None
    ]
    autonomous_execution_idle_seconds = (
        min(autonomous_execution_idle_candidates)
        if autonomous_execution_idle_candidates
        else None
    )

    counts = dict(snapshot.get("counts") or {})
    raw_error_count = snapshot.get("error_count")
    if raw_error_count is None:
        raw_error_count = counts.get("error_count") or counts.get("recent_errors")
    raw_uncertainty_count = snapshot.get("uncertainty_high_count")
    if raw_uncertainty_count is None:
        raw_uncertainty_count = counts.get("uncertainty_high_count") or counts.get(
            "high_uncertainty"
        )
    try:
        error_count = int(raw_error_count) if raw_error_count is not None else 0
    except (TypeError, ValueError):
        error_count = 0
    try:
        uncertainty_count = (
            int(raw_uncertainty_count) if raw_uncertainty_count is not None else 0
        )
    except (TypeError, ValueError):
        uncertainty_count = 0

    if user_idle_seconds is None:
        user_idle_hours = 24.0
    else:
        user_idle_hours = min(user_idle_seconds / 3600.0, 24.0)
    decay_factor = max(0.0, 1.0 - user_idle_hours / 4.0)
    correction_signals = int(round((error_count + uncertainty_count) * decay_factor))

    user_chain_quiet = (
        user_idle_seconds is None
        or user_idle_seconds >= config.user_idle_seconds
    )
    has_memory_idle = (
        memory_idle_seconds is None
        or memory_idle_seconds >= config.memory_idle_seconds
    )
    has_employee_execution_idle = (
        active_cli_execution_is_stale
        and (
            autonomous_execution_idle_seconds is None
            or autonomous_execution_idle_seconds >= config.workflow_idle_seconds
        )
    )
    has_self_learning_idle = (
        self_learning_idle_seconds is None
        or self_learning_idle_seconds >= config.workflow_idle_seconds
    )
    has_autonomous_chain_plan_idle = (
        autonomous_chain_plan_idle_seconds is None
        or autonomous_chain_plan_idle_seconds >= config.workflow_idle_seconds
    )
    has_autonomous_chain_execute_idle = (
        autonomous_chain_execute_idle_seconds is None
        or autonomous_chain_execute_idle_seconds >= config.workflow_idle_seconds
    )
    has_autonomous_chain_idle = (
        autonomous_chain_idle_seconds is None
        or autonomous_chain_idle_seconds >= config.workflow_idle_seconds
    )

    active_sessions = int(snapshot.get("active_sessions") or 0)
    if config.perception_scope == "autonomous_only":
        user_chain_signal = {
            "scope": "excluded_in_auto",
            "observed": False,
            "active_sessions": 0,
            "is_quiet": True,
            "recent_user_idle_seconds": None,
            "quiet_after_seconds": config.user_idle_seconds,
        }
    else:
        user_chain_signal = {
            "scope": "soft_signal_only",
            "active_sessions": active_sessions,
            "is_quiet": bool(user_chain_quiet and active_sessions <= 0),
            "recent_user_idle_seconds": user_idle_seconds,
            "quiet_after_seconds": config.user_idle_seconds,
        }
    governance_task_type_decisions = {
        "user": {
            "eligible_for_planning": config.perception_scope != "autonomous_only",
            "eligible_for_execution": config.perception_scope != "autonomous_only",
        },
        "self_learning": {
            "eligible_for_planning": True,
            "eligible_for_execution": (
                has_employee_execution_idle
                and has_memory_idle
                and has_self_learning_idle
                and has_autonomous_chain_plan_idle
            ),
        },
        "memory_maintenance": {
            "eligible_for_planning": True,
            "eligible_for_execution": has_memory_idle,
        },
        "self_evolution": {
            "eligible_for_planning": has_autonomous_chain_plan_idle,
            "eligible_for_execution": (
                has_employee_execution_idle
                and has_memory_idle
                and has_autonomous_chain_plan_idle
                and has_autonomous_chain_execute_idle
            ),
        },
    }
    task_family_decisions = {
        "user": dict(governance_task_type_decisions["user"]),
        "self_learning": dict(governance_task_type_decisions["self_learning"]),
        "memory_maintenance": dict(governance_task_type_decisions["memory_maintenance"]),
        "general_self_evolution": dict(governance_task_type_decisions["self_evolution"]),
        "body_upgrade": dict(governance_task_type_decisions["self_evolution"]),
        "body_switch": dict(governance_task_type_decisions["self_evolution"]),
    }
    selected_task_decisions = task_family_decisions[requested_task_family]

    if config.autonomous_chain_gate_active:
        governance_task_type_decisions["self_learning"]["eligible_for_planning"] = True
        governance_task_type_decisions["memory_maintenance"]["eligible_for_planning"] = True
        task_family_decisions["self_learning"]["eligible_for_planning"] = True
        task_family_decisions["memory_maintenance"]["eligible_for_planning"] = True

    return {
        "status": "evaluated",
        "evaluated_at": config.now.isoformat(),
        "gateway_address": config.gateway_address,
        "governance_task_type": requested_governance_task_type,
        "task_family": requested_task_family,
        "execution_kind": task_profile.get("execution_kind"),
        "task_profile": dict(task_profile),
        "activity": dict(snapshot),
        "shell_slot": dict(shell_slot) if shell_slot is not None else None,
        "completed_learning_tasks": [dict(item) for item in completed_learning_tasks],
        "correction_signals": correction_signals,
        "error_count": error_count,
        "uncertainty_high_count": uncertainty_count,
        "correction_signal_decay": {
            "factor": round(decay_factor, 4),
            "user_idle_hours": round(user_idle_hours, 2),
            "half_life_hours": 4.0,
        },
        "idle_seconds": {
            "user": user_idle_seconds,
            "employee_execution": autonomous_execution_idle_seconds,
            "memory": memory_idle_seconds,
            "self_learning": self_learning_idle_seconds,
            "autonomous_chain_plan": autonomous_chain_plan_idle_seconds,
            "autonomous_chain_execute": autonomous_chain_execute_idle_seconds,
            "autonomous_chain": autonomous_chain_idle_seconds,
        },
        "thresholds": {
            "user_idle_seconds": config.user_idle_seconds,
            "memory_idle_seconds": config.memory_idle_seconds,
            "workflow_idle_seconds": config.workflow_idle_seconds,
            "cli_lease_stale_after_seconds": dict(
                snapshot.get("active_cli_executor") or {}
            ).get("stale_after_seconds"),
        },
        "user_chain_signal": user_chain_signal,
        "checks": {
            "has_memory_idle": has_memory_idle,
            "has_employee_execution_idle": has_employee_execution_idle,
            "has_self_learning_idle": has_self_learning_idle,
            "has_autonomous_chain_plan_idle": has_autonomous_chain_plan_idle,
            "has_autonomous_chain_execute_idle": has_autonomous_chain_execute_idle,
            "has_autonomous_chain_idle": has_autonomous_chain_idle,
        },
        "governance_task_type_decisions": governance_task_type_decisions,
        "task_family_decisions": task_family_decisions,
        "autonomous_chain_gate_active": config.autonomous_chain_gate_active,
        "perception_scope": config.perception_scope,
        "evidence_packet": (
            dict(config.evidence_packet)
            if config.perception_scope == "autonomous_only"
            else {}
        ),
        "input_policy": {
            "scope": config.perception_scope,
            "user_activity_observed": config.perception_scope != "autonomous_only",
            "desktop_environment_observed": False,
        },
        "decisions": {
            "eligible_for_planning": selected_task_decisions["eligible_for_planning"],
            "eligible_for_execution": selected_task_decisions["eligible_for_execution"],
        },
    }


__all__ = [
    "DriveInputEvaluationConfig",
    "evaluate_drive_input_snapshot",
]

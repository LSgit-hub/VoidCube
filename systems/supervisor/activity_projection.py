"""Pure activity and Auto-drive input projections for the Supervisor."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


def parse_activity_timestamp(value: Any) -> datetime | None:
    """Parse gateway timestamps into the naive-UTC comparison clock."""
    if not value or not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is not None:
        return parsed.astimezone(timezone.utc).replace(tzinfo=None)
    return parsed


def idle_seconds_since(timestamp: datetime | None, *, now: datetime) -> float | None:
    """Return a non-negative idle duration in the timestamp comparison clock."""
    if timestamp is None:
        return None
    return max((now - timestamp).total_seconds(), 0.0)


def project_runtime_observation_input(
    payload: dict[str, Any] | None,
    *,
    snapshot_source: str = "live",
) -> dict[str, Any]:
    """Normalize a runtime activity payload into the observation input contract."""
    raw = dict(payload or {})
    activity = dict(raw.get("activity") or {})
    counts = dict(activity.get("counts") or {})
    recent_metadata = dict(activity.get("recent_metadata") or {})
    active_sessions_raw = raw.get("active_sessions")
    if active_sessions_raw is None:
        active_sessions_raw = activity.get("active_sessions")
    try:
        active_sessions = max(0, int(active_sessions_raw or 0))
    except (TypeError, ValueError):
        active_sessions = 0

    user_chain_signal = dict(raw.get("user_chain_signal") or {})
    quiet_after_raw = user_chain_signal.get("quiet_after_seconds")
    try:
        quiet_after_seconds = max(0, int(quiet_after_raw or 600))
    except (TypeError, ValueError):
        quiet_after_seconds = 600

    user_chain_signal["scope"] = (
        str(user_chain_signal.get("scope") or "soft_signal_only").strip()
        or "soft_signal_only"
    )
    user_chain_signal["active_sessions"] = active_sessions
    user_chain_signal["quiet_after_seconds"] = quiet_after_seconds
    if "is_quiet" not in user_chain_signal:
        user_chain_signal["is_quiet"] = active_sessions <= 0

    activity["active_sessions"] = active_sessions
    activity["counts"] = counts
    activity["recent_metadata"] = recent_metadata
    return {
        "activity": activity,
        "user_chain_signal": user_chain_signal,
        "snapshot_source": str(snapshot_source or "live").strip() or "live",
    }


def project_auto_activity_snapshot(source: dict[str, Any] | None) -> dict[str, Any]:
    """Keep only Supervisor-owned activity signals for an Auto drive cycle."""
    source = dict(source or {})
    allowed_timestamps = (
        "last_memory_task_at",
        "last_self_learning_activity_at",
        "last_autonomous_chain_plan_at",
        "last_autonomous_chain_execute_at",
        "last_autonomous_chain_activity_at",
    )
    projected = {
        key: source.get(key)
        for key in allowed_timestamps
        if source.get(key) is not None
    }
    executor = dict(source.get("active_cli_executor") or {})
    if str(executor.get("agent_lane") or "").strip().lower() == "supervisor_task":
        projected["active_cli_executor"] = {
            key: executor.get(key)
            for key in (
                "agent_lane",
                "lease_status",
                "idle_seconds",
                "is_stale",
                "stale_after_seconds",
            )
            if key in executor
        }
    projected.update(
        {
            "active_sessions": 0,
            "counts": {},
            "recent_metadata": {},
            "perception_scope": "autonomous_only",
        }
    )
    return projected


def enforce_auto_drive_input_boundary(
    source: dict[str, Any] | None,
    *,
    evidence_packet: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Remove user-facing signals before an input crosses into Auto."""
    payload = dict(source or {})
    payload["activity"] = project_auto_activity_snapshot(payload.get("activity"))
    payload["perception_scope"] = "autonomous_only"
    payload["evidence_packet"] = dict(evidence_packet or {})
    payload["active_sessions"] = 0
    payload["correction_signals"] = 0
    payload["error_count"] = 0
    payload["uncertainty_high_count"] = 0
    payload["user_chain_signal"] = {
        "scope": "excluded_in_auto",
        "observed": False,
        "active_sessions": 0,
        "is_quiet": True,
        "recent_user_idle_seconds": None,
        "quiet_after_seconds": payload.get("thresholds", {}).get("user_idle_seconds", 600),
    }
    idle_seconds = dict(payload.get("idle_seconds") or {})
    idle_seconds["user"] = None
    payload["idle_seconds"] = idle_seconds
    decisions = dict(payload.get("governance_task_type_decisions") or {})
    if "user" in decisions:
        decisions["user"] = {
            "eligible_for_planning": False,
            "eligible_for_execution": False,
        }
        payload["governance_task_type_decisions"] = decisions
    task_decisions = dict(payload.get("task_family_decisions") or {})
    if "user" in task_decisions:
        task_decisions["user"] = {
            "eligible_for_planning": False,
            "eligible_for_execution": False,
        }
        payload["task_family_decisions"] = task_decisions
    return payload

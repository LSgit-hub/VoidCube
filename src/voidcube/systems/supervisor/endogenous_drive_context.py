"""Pure normalization of endogenous drive input and strategy memory."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

from systems.supervisor.endogenous_learning import topic_signature


_TERMINAL_QUEUE_STATUSES = {"completed", "failed", "cancelled"}
_REVIEW_BACKLOG_STATUSES = {"deferred", "paused", "awaiting_review", "retry"}


def normalize_drive_input(value: Any) -> Dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _clamp01(value: Any) -> float:
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return 0.0


def parse_timestamp(raw_timestamp: Any) -> Optional[datetime]:
    if not raw_timestamp:
        return None
    try:
        parsed = datetime.fromisoformat(str(raw_timestamp))
    except Exception:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def get_shell_slot_meta(drive_input: Dict[str, Any]) -> Dict[str, Any]:
    raw_shell_slot = drive_input.get("shell_slot")
    if isinstance(raw_shell_slot, dict):
        return dict(raw_shell_slot)
    return {}


def normalize_strategy_memory(raw: Any) -> Dict[str, Any]:
    source = dict(raw or {}) if isinstance(raw, dict) else {}
    focus_stats: Dict[str, Dict[str, int]] = {}
    contextual_focus_stats: Dict[str, Dict[str, Dict[str, int]]] = {}
    agenda_topic_stats: Dict[str, Dict[str, Any]] = {}
    observation_target_stats: Dict[str, Dict[str, Any]] = {}

    raw_focus_stats = source.get("focus_stats")
    if isinstance(raw_focus_stats, dict):
        for focus, stats in raw_focus_stats.items():
            focus_name = str(focus or "").strip().lower()
            if not focus_name or not isinstance(stats, dict):
                continue
            focus_stats[focus_name] = {
                "judged": max(0, int(stats.get("judged") or 0)),
                "completed": max(0, int(stats.get("completed") or 0)),
                "failed": max(0, int(stats.get("failed") or 0)),
                "dragging": max(0, int(stats.get("dragging") or 0)),
            }

    raw_contextual = source.get("contextual_focus_stats")
    if isinstance(raw_contextual, dict):
        for context_key, focus_map in raw_contextual.items():
            normalized_context = str(context_key or "").strip().lower()
            if not normalized_context or not isinstance(focus_map, dict):
                continue
            context_bucket: Dict[str, Dict[str, int]] = {}
            for focus, stats in focus_map.items():
                focus_name = str(focus or "").strip().lower()
                if not focus_name or not isinstance(stats, dict):
                    continue
                context_bucket[focus_name] = {
                    "judged": max(0, int(stats.get("judged") or 0)),
                    "completed": max(0, int(stats.get("completed") or 0)),
                    "failed": max(0, int(stats.get("failed") or 0)),
                    "dragging": max(0, int(stats.get("dragging") or 0)),
                }
            if context_bucket:
                contextual_focus_stats[normalized_context] = context_bucket

    raw_agenda_topic_stats = source.get("agenda_topic_stats")
    if isinstance(raw_agenda_topic_stats, dict):
        for topic, stats in raw_agenda_topic_stats.items():
            topic_name = str(topic or "").strip().lower()
            if not topic_name or not isinstance(stats, dict):
                continue
            agenda_topic_stats[topic_name] = {
                "seen": max(0, int(stats.get("seen") or 0)),
                "active_cycles": max(0, int(stats.get("active_cycles") or 0)),
                "resolved": max(0, int(stats.get("resolved") or 0)),
                "dragging": max(0, int(stats.get("dragging") or 0)),
                "last_priority": round(_clamp01(stats.get("last_priority") or 0.0), 4),
                "last_confidence": round(_clamp01(stats.get("last_confidence") or 0.0), 4),
                "last_status": str(stats.get("last_status") or "unknown").strip().lower() or "unknown",
                "last_seen_at": stats.get("last_seen_at"),
                "last_resolved_at": stats.get("last_resolved_at"),
                "last_context_key": str(stats.get("last_context_key") or "").strip().lower() or None,
            }

    raw_observation_target_stats = source.get("observation_target_stats")
    if isinstance(raw_observation_target_stats, dict):
        for target, stats in raw_observation_target_stats.items():
            target_name = str(target or "").strip().lower()
            if not target_name or not isinstance(stats, dict):
                continue
            observation_target_stats[target_name] = {
                "seen": max(0, int(stats.get("seen") or 0)),
                "recommended": max(0, int(stats.get("recommended") or 0)),
                "resolved": max(0, int(stats.get("resolved") or 0)),
                "stalled": max(0, int(stats.get("stalled") or 0)),
                "last_priority": round(_clamp01(stats.get("last_priority") or 0.0), 4),
                "last_risk": round(_clamp01(stats.get("last_risk") or 0.0), 4),
                "last_status": str(stats.get("last_status") or "unknown").strip().lower() or "unknown",
                "last_seen_at": stats.get("last_seen_at"),
                "last_resolved_at": stats.get("last_resolved_at"),
                "last_context_key": str(stats.get("last_context_key") or "").strip().lower() or None,
            }
    return {
        "focus_stats": focus_stats,
        "contextual_focus_stats": contextual_focus_stats,
        "agenda_topic_stats": agenda_topic_stats,
        "observation_target_stats": observation_target_stats,
    }


def build_drive_context(
    drive_input: Dict[str, Any],
    *,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    policy = dict(drive_input.get("endogenous_drive_policy") or {})
    drive_history = dict(drive_input.get("drive_history") or {})
    api_b_judgement_tasks = list(drive_input.get("api_b_judgement_tasks") or [])
    api_a_execution_lane_tasks = list(drive_input.get("api_a_execution_lane_tasks") or [])
    autonomous_chain_live_tasks = list(
        drive_input.get("autonomous_chain_live_tasks")
        or [*api_b_judgement_tasks, *api_a_execution_lane_tasks]
    )
    completed_learning_tasks = list(drive_input.get("completed_learning_tasks") or [])
    recent_learning_titles = [
        str(task.get("title") or "").strip()
        for task in completed_learning_tasks
        if str(task.get("title") or "").strip()
    ]
    learning_backlog_titles = []
    body_improvement_backlog_titles = []
    signatures = []
    active_backlog_tasks = []
    active_backlog_by_governance: Dict[str, int] = {}
    active_backlog_by_family: Dict[str, int] = {}
    active_backlog_by_execution_kind: Dict[str, int] = {}
    stale_backlog_count = 0
    pending_review_count = 0
    api_a_handoff_count = 0
    api_a_running_count = 0
    current_time = now or datetime.now(timezone.utc)

    for title in recent_learning_titles:
        signatures.append(topic_signature(title))
    for task in autonomous_chain_live_tasks:
        title = str(task.get("title") or "").strip()
        if not title:
            continue
        status = str(task.get("status") or "").strip().lower()
        execution_kind = str(task.get("execution_kind") or "").strip().lower()
        governance_task_type = str(task.get("governance_task_type") or "").strip().lower()
        task_family = str(task.get("task_family") or "").strip().lower()
        if task_family == "self_learning" and status not in _TERMINAL_QUEUE_STATUSES:
            learning_backlog_titles.append(title)
            signatures.append(topic_signature(title))
        if execution_kind == "body_improvement":
            body_improvement_backlog_titles.append(title)
        if status not in _TERMINAL_QUEUE_STATUSES and task in api_b_judgement_tasks:
            active_backlog_tasks.append(task)
            if governance_task_type:
                active_backlog_by_governance[governance_task_type] = active_backlog_by_governance.get(governance_task_type, 0) + 1
            if task_family:
                active_backlog_by_family[task_family] = active_backlog_by_family.get(task_family, 0) + 1
            if execution_kind:
                active_backlog_by_execution_kind[execution_kind] = active_backlog_by_execution_kind.get(execution_kind, 0) + 1
            if status in _REVIEW_BACKLOG_STATUSES:
                pending_review_count += 1
            updated_at = parse_timestamp(task.get("updated_at") or task.get("created_at"))
            if updated_at is not None and current_time - updated_at >= timedelta(hours=24):
                stale_backlog_count += 1
    for task in api_a_execution_lane_tasks:
        status = str(task.get("status") or "").strip().lower()
        if status in {"approved", "retry"}:
            api_a_handoff_count += 1
        elif status == "running":
            api_a_running_count += 1
    return {
        "policy": policy,
        "evolution_foundation": dict(drive_input.get("evolution_foundation") or {}),
        "drive_history": {
            "judgements": [dict(item) for item in list(drive_history.get("judgements") or []) if isinstance(item, dict)],
            "outcomes": [dict(item) for item in list(drive_history.get("outcomes") or []) if isinstance(item, dict)],
            "strategy_memory": normalize_strategy_memory(drive_history.get("strategy_memory")),
        },
        "api_b_judgement_tasks": api_b_judgement_tasks,
        "api_a_execution_lane_tasks": api_a_execution_lane_tasks,
        "autonomous_chain_live_tasks": autonomous_chain_live_tasks,
        "completed_learning_tasks": completed_learning_tasks,
        "recent_learning_titles": recent_learning_titles,
        "learning_backlog_titles": learning_backlog_titles,
        "body_improvement_backlog_titles": body_improvement_backlog_titles,
        "recent_learning_signatures": signatures,
        "api_b_judgement_count": len(active_backlog_tasks),
        "active_backlog_by_governance": active_backlog_by_governance,
        "active_backlog_by_family": active_backlog_by_family,
        "active_backlog_by_execution_kind": active_backlog_by_execution_kind,
        "stale_backlog_count": stale_backlog_count,
        "pending_review_count": pending_review_count,
        "api_a_handoff_count": api_a_handoff_count,
        "api_a_ready_count": api_a_handoff_count,
        "api_a_running_count": api_a_running_count,
    }

"""Pure projections consumed by the Supervisor web UI."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any


def format_supervisor_ui_event(event_name: str, payload: dict[str, Any]) -> str:
    data = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    return f"event: {event_name}\ndata: {data}\n\n"


def default_observation_input_snapshot() -> dict[str, Any]:
    return {
        "activity": {"active_sessions": 0, "counts": {}, "recent_metadata": {}},
        "user_chain_signal": {
            "scope": "soft_signal_only",
            "active_sessions": 0,
            "is_quiet": True,
            "quiet_after_seconds": 600,
        },
        "snapshot_source": "default",
    }


def activity_source_label(value: Any) -> str:
    normalized = str(value or "").strip().lower()
    return {
        "supervisor": "API-B",
        "agent": "API-A",
        "executor": "API-A 子执行面",
        "memory": "Mem",
        "gateway": "网关",
    }.get(normalized, str(value or "").strip() or "未知侧")


def runtime_activity_label(value: Any) -> str:
    normalized = str(value or "").strip().lower()
    return {
        "self_learning": "自主学习",
        "self_evolution": "自主改进",
        "general_self_evolution": "通用自主改进",
        "memory_maintenance": "记忆维护",
        "body_upgrade": "替身升级",
        "body_switch": "身体切换",
        "body_improvement": "替身改进",
        "autonomous_chain": "自主链路",
        "autonomous_chain_plan": "自主链路规划",
        "autonomous_chain_execute": "自主链路执行",
    }.get(normalized, str(value or "").strip() or "未命名动作")


def project_recent_autonomous_activity(
    activity_snapshot: dict[str, Any],
) -> dict[str, Any]:
    if not isinstance(activity_snapshot, dict):
        return {}

    recent_metadata = dict(activity_snapshot.get("recent_metadata") or {})

    def parse_iso_token(value: Any) -> datetime | None:
        text = str(value or "").strip()
        if not text:
            return None
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            return None
        if parsed.tzinfo is not None:
            parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
        return parsed

    candidates = [
        (
            "autonomous_chain_execute",
            parse_iso_token(activity_snapshot.get("last_autonomous_chain_execute_at")),
            "执行回报",
            "accent",
        ),
        (
            "autonomous_chain_plan",
            parse_iso_token(activity_snapshot.get("last_autonomous_chain_plan_at")),
            "判断转交",
            "info",
        ),
        (
            "self_learning",
            parse_iso_token(activity_snapshot.get("last_self_learning_activity_at")),
            "自主学习",
            "accent",
        ),
        (
            "memory_write_failure",
            parse_iso_token(activity_snapshot.get("last_memory_write_failure_at")),
            "写回异常",
            "warn",
        ),
        (
            "autonomous_chain",
            parse_iso_token(activity_snapshot.get("last_autonomous_chain_activity_at")),
            "最近动作",
            "info",
        ),
    ]
    newest: tuple[str, datetime, str, str, dict[str, Any]] | None = None
    for kind, recorded_at, phase_label, tone in candidates:
        if recorded_at is None:
            continue
        metadata = dict(recent_metadata.get(kind) or {})
        if not metadata:
            continue
        if newest is None or recorded_at > newest[1]:
            newest = (kind, recorded_at, phase_label, tone, metadata)

    if newest is None:
        return {
            "kind": "unavailable",
            "phase_label": "最近自主动作",
            "title": "最近暂无自主链路动作",
            "summary": "等待新的候选、回报或 Mem 回流。",
            "source_label": "API-B",
            "tone": "info",
        }

    kind, recorded_at, phase_label, tone, metadata = newest
    identity = dict(metadata.get("task_identity") or {})
    label = (
        str(identity.get("display_label") or "").strip()
        or str(metadata.get("execution_kind_label") or "").strip()
        or str(metadata.get("task_family_label") or "").strip()
        or str(metadata.get("governance_task_type_label") or "").strip()
        or str(metadata.get("task_type_label") or "").strip()
        or runtime_activity_label(metadata.get("kind"))
        or runtime_activity_label(metadata.get("execution_kind"))
        or runtime_activity_label(metadata.get("task_family"))
        or runtime_activity_label(metadata.get("governance_task_type"))
        or runtime_activity_label(metadata.get("task_type"))
    )
    title = (
        str(identity.get("summary") or "").strip()
        or str(metadata.get("title") or metadata.get("task_title") or "").strip()
        or label
        or phase_label
    )
    source_label = activity_source_label(metadata.get("source_service"))
    if kind == "autonomous_chain_execute":
        summary = f"{source_label} 已向 API-B 回报 {label or '自主链路项'} 的执行进展。"
    elif kind == "autonomous_chain_plan":
        summary = f"API-B 已更新 {label or '自主链路项'} 的判断，并决定是否转交 API-A。"
    elif kind == "self_learning":
        summary = f"API-A 子执行面正在围绕 {label or '自主学习'} 回传学习进展，供 API-B 后续吸收。"
    elif kind == "memory_write_failure":
        summary = "最近一次 Mem 写回回流出现异常，当前闭环需要补偿或重试。"
    else:
        summary = f"{source_label} 最近记下了一次会影响自主闭环下一跳的动作。"

    return {
        "kind": kind,
        "phase_label": phase_label,
        "title": title,
        "summary": summary,
        "source_label": source_label,
        "recorded_at": recorded_at.isoformat(),
        "display_label": label,
        "tone": tone,
    }


def observation_count(value: Any) -> int:
    try:
        return max(int(value or 0), 0)
    except Exception:
        return 0


def observation_group(observation: dict[str, Any], key: str) -> dict[str, Any]:
    chain = dict(observation.get("chain") or {})
    for group in list(chain.get("segments") or []):
        if isinstance(group, dict) and str(group.get("key") or "").strip() == key:
            return dict(group)
    return {}


def observation_loop_stage(observation: dict[str, Any], key: str) -> dict[str, Any]:
    loop = dict(observation.get("loop") or {})
    normalized_key = str(key or "").strip()
    for stage_card in list(loop.get("stage_cards") or []):
        if not isinstance(stage_card, dict):
            continue
        if str(stage_card.get("stage_key") or "").strip() != normalized_key:
            continue
        projected = dict(stage_card)
        projected["key"] = normalized_key
        if not str(projected.get("status_label") or "").strip():
            projected["status_label"] = str(
                projected.get("display_status") or ""
            ).strip()
        if "focus_task" not in projected:
            projected["focus_task"] = dict(stage_card.get("focus_task") or {})
        return projected
    return {}


def project_observation_board(
    observation: dict[str, Any],
    *,
    recent_activity: dict[str, Any],
) -> dict[str, Any]:
    counts = dict(observation.get("counts") or {})
    board = dict(observation.get("board") or {})
    running_count = observation_count(counts.get("api_a_running"))
    board["recent_activity"] = dict(recent_activity)
    board["hero_summary"] = str(
        board.get("hero_summary") or board.get("summary") or "只看当前落点和回流。"
    ).strip()
    notes: list[dict[str, Any]] = []
    if running_count:
        notes.append(
            {
                "key": "api_a_flow_hold",
                "tone": "info",
                "title": "API-A 执行中",
                "text": f"还有 {running_count} 个执行中链路项，写回后会回到这里。",
            }
        )
    board["observation_notes"] = notes
    return board

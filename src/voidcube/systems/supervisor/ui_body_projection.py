"""Pure body-slot and upgrade projections for the Supervisor web UI."""

from __future__ import annotations

from typing import Any

from .observation_status import observation_status_label
from .ui_observation_projection import normalize_observation_status


def body_slot_role_label(
    slot_id: str,
    *,
    active_slot: str,
    shell_slot: str,
    retired_slot: str,
) -> str:
    if slot_id and slot_id == active_slot:
        return "当前替身"
    if slot_id and slot_id == shell_slot:
        return "培养替身"
    if slot_id and slot_id == retired_slot:
        return "退役替身"
    return "替身槽位"


def body_slot_state_label(value: Any) -> str:
    normalized = str(value or "").strip().lower()
    return {
        "active": "在用",
        "shell": "待培养",
        "candidate": "候选中",
        "probe": "验证中",
        "retired": "已退役",
    }.get(normalized, str(value or "").strip() or "未知")


def _health_label(healthy: Any, healthy_label: str, unhealthy_label: str, unknown_label: str = "未知") -> str:
    if healthy is True:
        return healthy_label
    if healthy is False:
        return unhealthy_label
    return unknown_label


def _runtime_health(meta: dict[str, Any], *, active_slot: str, slot_id: str) -> dict[str, Any]:
    state = str(meta.get("body_state") or "").strip().lower()
    if slot_id == active_slot or state == "active":
        return {"status": "active", "label": "当前运行", "healthy": True}
    if state in {"candidate", "probe", "awaiting_user_consent"}:
        return {"status": state, "label": body_slot_state_label(state), "healthy": None}
    if state == "shell":
        return {"status": "shell", "label": "待培养", "healthy": None}
    if state == "retired":
        return {"status": "retired", "label": "已退役", "healthy": None}
    return {"status": state or "unknown", "label": "未运行", "healthy": False}


def _improvement_progress(
    meta: dict[str, Any],
    signals: list[dict[str, Any]],
    *,
    shell_slot: str,
) -> dict[str, Any]:
    state = str(meta.get("body_state") or "").strip().lower()
    if signals:
        signal = signals[0]
        return {
            "status": str(signal.get("status") or "planned"),
            "label": str(signal.get("status_label") or "改进中"),
            "active": True,
            "signals_count": len(signals),
        }
    if state in {"candidate", "probe", "awaiting_user_consent"}:
        return {"status": state, "label": body_slot_state_label(state), "active": True, "signals_count": 0}
    if shell_slot and str(meta.get("slot_id") or "") == shell_slot:
        return {"status": "waiting", "label": "等待培养", "active": False, "signals_count": 0}
    return {"status": "stable", "label": "暂无改进", "active": False, "signals_count": 0}


def body_upgrade_signal_source_label(status: str) -> str:
    normalized = str(status or "").strip().lower()
    if normalized == "running":
        return "员工代理 正在改"
    if normalized in {"approved", "retry"}:
        return "API-B 已转交"
    return "API-B 正在安排"


def body_upgrade_task_target_slot(task: dict[str, Any]) -> str:
    execution = dict(task.get("execution_request") or {})
    metadata = dict(task.get("metadata") or {})
    constraints = dict(task.get("constraints") or {})
    return str(
        execution.get("target_slot_id")
        or metadata.get("target_slot_id")
        or constraints.get("target_slot_id")
        or ""
    ).strip()


def body_upgrade_task_node_keys(task: dict[str, Any]) -> list[str]:
    execution = dict(task.get("execution_request") or {})
    metadata = dict(task.get("metadata") or {})
    constraints = dict(task.get("constraints") or {})
    raw_paths: list[Any] = []
    raw_paths.extend(list(execution.get("editable_dirs") or []))
    raw_paths.extend(list(metadata.get("editable_dirs") or []))
    raw_paths.extend(list(constraints.get("editable_dirs") or []))
    raw_paths.extend(list(task.get("changed_files") or []))
    raw_paths.extend(list(metadata.get("changed_files") or []))
    seen: list[str] = []
    for value in raw_paths:
        text = str(value or "").strip().replace("\\", "/")
        if not text:
            continue
        text = text.lstrip("./").strip("/")
        if not text or text in {".", ".."}:
            continue
        parts = [
            part.strip()
            for part in text.split("/")
            if part.strip() and part.strip() not in {".", ".."}
        ]
        for index in range(1, min(len(parts), 4) + 1):
            key = "/".join(parts[:index]).strip()
            if key and key not in seen:
                seen.append(key)
    return seen


def body_tree_node_label(node_key: str) -> str:
    normalized = str(node_key or "").strip().replace("\\", "/")
    if not normalized:
        return ""
    if "/" not in normalized:
        return normalized
    return normalized.rsplit("/", 1)[-1] or normalized


def project_body_upgrade_signal_map(
    chain_history_projection: list[dict[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    by_slot: dict[str, list[dict[str, Any]]] = {}
    visible_statuses = {
        "planned", "approved", "retry", "running", "awaiting_user_consent",
    }
    for task in chain_history_projection:
        if str(task.get("execution_kind") or "").strip().lower() != "body_improvement":
            continue
        status = normalize_observation_status(task.get("status"))
        if status not in visible_statuses:
            continue
        target_slot_id = body_upgrade_task_target_slot(task)
        if not target_slot_id:
            continue
        node_keys = body_upgrade_task_node_keys(task) or ["agent"]
        by_slot.setdefault(target_slot_id, []).append(
            {
                "task_id": str(task.get("task_id") or "").strip(),
                "title": str(task.get("title") or "替身改进任务").strip() or "替身改进任务",
                "status": status,
                "status_label": observation_status_label(status),
                "source_label": body_upgrade_signal_source_label(status),
                "node_keys": node_keys,
            }
        )
    return by_slot


def project_body_slot_cards(
    *,
    registry: dict[str, Any],
    slot_metas: dict[str, dict[str, Any]],
    chain_history_projection: list[dict[str, Any]],
    integrity_report: dict[str, Any] | None = None,
    top_level_entries_by_slot: dict[str, list[str]] | None = None,
) -> list[dict[str, Any]]:
    signal_map = project_body_upgrade_signal_map(chain_history_projection)
    active_slot = str(registry.get("active_slot") or "").strip()
    shell_slot = str(registry.get("shell_slot") or "").strip()
    retired_slot = str(registry.get("retired_slot") or "").strip()
    ordered_slot_ids: list[str] = []
    for slot_id in [active_slot, shell_slot, retired_slot, *list(registry.get("slot_ids") or [])]:
        normalized = str(slot_id or "").strip()
        if normalized and normalized not in ordered_slot_ids:
            ordered_slot_ids.append(normalized)

    integrity = dict(integrity_report or {})
    integrity_slots = dict(integrity.get("slots") or {})
    integrity_violations = [
        dict(item)
        for item in list(integrity.get("violations") or [])
        if isinstance(item, dict)
    ]
    cards: list[dict[str, Any]] = []
    for slot_id in ordered_slot_ids:
        meta = dict(slot_metas.get(slot_id) or {})
        if not meta:
            continue
        slot_integrity = dict(integrity_slots.get(slot_id) or {})
        slot_violations = [
            item for item in integrity_violations
            if str(item.get("slot_id") or "").strip() == slot_id
        ]
        signals = list(signal_map.get(slot_id) or [])
        readiness = dict(meta.get("body_readiness") or {})
        structure_healthy = slot_integrity.get("healthy")
        if structure_healthy is None:
            structure_healthy = bool(readiness.get("checks", {}).get("worktree_exists")) or None
        runtime_health = _runtime_health(meta, active_slot=active_slot, slot_id=slot_id)
        code_checks = dict(readiness.get("checks") or {})
        code_healthy = readiness.get("ready")
        improvement_progress = _improvement_progress(meta, signals, shell_slot=shell_slot)
        signal_node_keys: list[str] = []
        for signal in signals:
            for node_key in list(signal.get("node_keys") or []):
                normalized = str(node_key or "").strip()
                if normalized and normalized not in signal_node_keys:
                    signal_node_keys.append(normalized)
        visible_node_keys: list[str] = []
        structure_entries = [
            str(entry or "").strip().replace("\\", "/").strip("/")
            for entry in list((top_level_entries_by_slot or {}).get(slot_id) or [])
        ]
        for node_key in [
            *signal_node_keys,
            *structure_entries,
        ]:
            normalized = str(node_key or "").strip()
            if normalized and normalized not in visible_node_keys:
                visible_node_keys.append(normalized)
        tree_nodes: list[dict[str, Any]] = []
        for node_key in visible_node_keys[:12]:
            matching_signals = [
                signal for signal in signals
                if node_key in list(signal.get("node_keys") or [])
            ]
            first_signal = dict(matching_signals[0]) if matching_signals else {}
            tree_nodes.append(
                {
                    "key": node_key,
                    "label": body_tree_node_label(node_key),
                    "upgrade_active": bool(matching_signals),
                    "upgrade_dot": bool(matching_signals),
                    "upgrade_status": str(first_signal.get("status") or "").strip(),
                    "upgrade_source": str(first_signal.get("source_label") or "").strip(),
                    "upgrade_task_id": str(first_signal.get("task_id") or "").strip(),
                    "upgrade_task_title": str(first_signal.get("title") or "").strip(),
                }
            )
        structure_roots = [entry for entry in structure_entries if not entry.startswith(".")]
        summary = " / ".join(structure_roots) if structure_roots else "结构待观察"
        if signals:
            focus_sources = "、".join(
                sorted({str(signal.get("source_label") or "").strip() for signal in signals if str(signal.get("source_label") or "").strip()})
            ) or "正在处理"
            focus_nodes = " / ".join(signal_node_keys[:3]) if signal_node_keys else "核心目录"
            focus_summary = f"{focus_sources} {focus_nodes}"
        elif slot_id == shell_slot:
            focus_summary = "培养替身，等待升级"
        elif slot_id == active_slot:
            focus_summary = "当前对外运行"
        else:
            focus_summary = "现在没有升级动作"
        cards.append(
            {
                "slot_id": slot_id,
                "role_label": body_slot_role_label(slot_id, active_slot=active_slot, shell_slot=shell_slot, retired_slot=retired_slot),
                "body_state": str(meta.get("body_state") or "").strip(),
                "body_state_label": body_slot_state_label(meta.get("body_state")),
                "body_version": str(meta.get("body_version") or "bootstrap").strip() or "bootstrap",
                "generation": int(meta.get("generation") or 0),
                "worktree_path": str(meta.get("worktree_path") or "").strip(),
                "summary": summary,
                "root_nodes": structure_roots,
                "focus_summary": focus_summary,
                "tree_nodes": tree_nodes,
                "upgrade_signals": signals[:3],
                "upgrade_active": bool(signals),
                "integrity_healthy": bool(slot_integrity.get("healthy")) if slot_integrity else None,
                "integrity_materialized": slot_integrity.get("materialized"),
                "integrity_violations": slot_violations,
                "readiness": readiness,
                "structure_health": {
                    "healthy": structure_healthy,
                    "label": _health_label(structure_healthy, "结构正常", "结构异常"),
                },
                "runtime_health": runtime_health,
                "code_health": {
                    "healthy": code_healthy,
                    "label": _health_label(code_healthy, "代码就绪", "代码未就绪"),
                    "head_commit": readiness.get("head_commit"),
                    "current_healthy_commit": meta.get("current_healthy_commit"),
                    "candidate_commit": meta.get("candidate_commit"),
                    "checks": code_checks,
                },
                "improvement_progress": improvement_progress,
                "health_score": float(meta.get("health_score") or 0.0),
                "current_healthy_commit": meta.get("current_healthy_commit"),
                "candidate_commit": meta.get("candidate_commit"),
            }
        )
    return cards

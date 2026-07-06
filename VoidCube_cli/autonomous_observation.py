from __future__ import annotations

from typing import Any, Dict


def observation_board(state: Dict[str, Any]) -> Dict[str, Any]:
    observation = dict(state.get("autonomous_observation") or {})
    return dict(observation.get("board") or {})


def observation_chain(state: Dict[str, Any]) -> Dict[str, Any]:
    observation = dict(state.get("autonomous_observation") or {})
    return dict(observation.get("chain") or {})


def observation_loop(state: Dict[str, Any]) -> Dict[str, Any]:
    observation = dict(state.get("autonomous_observation") or {})
    return dict(observation.get("loop") or {})


def observation_group_items(
    state: Dict[str, Any],
    group_key: str,
) -> list[Dict[str, Any]]:
    chain = observation_chain(state)
    groups = [
        dict(item)
        for item in list(chain.get("segments") or [])
        if isinstance(item, dict)
    ]
    for group in groups:
        if str(group.get("key") or "").strip() != group_key:
            continue
        return [
            dict(item)
            for item in list(group.get("items") or [])
            if isinstance(item, dict)
        ]
    return []


def observation_loop_stage_projections(state: Dict[str, Any]) -> list[Dict[str, Any]]:
    loop = observation_loop(state)
    projected: list[Dict[str, Any]] = []
    for stage in list(loop.get("stages") or []):
        if not isinstance(stage, dict):
            continue
        focus_task = dict(stage.get("focus_task") or {})
        projected.append(
            {
                **focus_task,
                "title": str(focus_task.get("title") or stage.get("label") or "阶段"),
                "status": str(stage.get("status") or focus_task.get("status") or "idle"),
                "display_status": str(
                    focus_task.get("display_status")
                    or stage.get("status_label")
                    or stage.get("label")
                    or "等待中"
                ),
                "summary": str(
                    focus_task.get("summary")
                    or stage.get("summary")
                    or stage.get("chain_reason")
                    or stage.get("activity_text")
                    or ""
                ),
                "observation_role": str(stage.get("key") or ""),
            }
        )
    return projected


def observation_loop_stage_projection(
    state: Dict[str, Any],
    *roles: str,
) -> Dict[str, Any]:
    wanted = {
        str(role or "").strip()
        for role in roles
        if str(role or "").strip()
    }
    if not wanted:
        return {}
    for item in observation_loop_stage_projections(state):
        role = str(item.get("observation_role") or "").strip()
        if role in wanted:
            return item
    return {}


def observation_loop_stage(
    state: Dict[str, Any],
    stage_key: str,
) -> Dict[str, Any]:
    loop = observation_loop(state)
    for stage in list(loop.get("stages") or []):
        if not isinstance(stage, dict):
            continue
        if str(stage.get("key") or "").strip() == str(stage_key or "").strip():
            return dict(stage)
    return {}


def _is_creativity_observation_task(task: Dict[str, Any]) -> bool:
    governance = str(task.get("governance_task_type") or "").strip().lower()
    task_family = str(task.get("task_family") or "").strip().lower()
    execution_kind = str(task.get("execution_kind") or "").strip().lower()
    return (
        governance == "self_learning"
        or task_family in {"self_learning", "body_upgrade"}
        or execution_kind == "body_improvement"
    )


def supervisor_api_a_execution_hint(supervisor_state: Dict[str, Any]) -> Dict[str, Any]:
    observation = dict(supervisor_state.get("autonomous_observation") or {})
    counts = dict(observation.get("counts") or {})
    api_a_stage = observation_loop_stage(supervisor_state, "api_a_execution")
    stage_label = str(api_a_stage.get("status_label") or "").strip()
    stage_reason = str(api_a_stage.get("chain_reason") or "").strip()
    stage_activity = str(api_a_stage.get("activity_text") or "").strip()
    stage_style = str(api_a_stage.get("reason_style") or "").strip().lower()
    api_a_ready_items = observation_group_items(supervisor_state, "api_a_ready")
    api_b_backlog = observation_group_items(supervisor_state, "api_b_backlog")
    creativity_governance = [
        task for task in api_b_backlog
        if _is_creativity_observation_task(task)
    ]
    focus_task = dict(api_a_stage.get("focus_task") or {})
    focus_status = str(focus_task.get("status") or "").strip().lower()
    approved_api_a = [
        task
        for task in api_a_ready_items
        if str(task.get("status") or "").strip().lower() in {"approved", "retry"}
    ]
    deferred_governance = [
        task
        for task in creativity_governance
        if str(task.get("status") or "").strip().lower() == "deferred"
    ]
    stage_projections = observation_loop_stage_projections(supervisor_state)
    chain_focus_cards = [
        dict(card)
        for card in stage_projections
        if str(card.get("observation_role") or "").strip()
        in {"api_b_judgement", "mem_writeback", "api_b_reread"}
        and str(card.get("status") or "").strip().lower() in {"active", "ready"}
    ]
    hint: Dict[str, Any] = {
        "stage": "idle",
        "cli_focus_stage": "idle",
        "focus_task": {},
        "status_label": "待命拉单",
        "chain_reason": "链路: 当前没有已批准的 API-A 可执行链路项",
        "activity_text": "执行流: 等待监督者放行链路项或等待下一轮拉单",
        "reason_style": "dim",
    }
    approved_focus = (
        dict(focus_task)
        if focus_status == "approved"
        else (dict(approved_api_a[0]) if approved_api_a else {})
    )
    if focus_task.get("task_id") and focus_status == "running":
        hint = {
            "stage": "running_autonomous_task",
            "cli_focus_stage": "running_elsewhere",
            "focus_task": dict(focus_task),
            "status_label": "他处执行中",
            "chain_reason": "链路: 该链路项已被其他 API-A 自主执行面认领",
            "activity_text": "执行流: 链路项正在其他 API-A 自主执行面中运行",
            "reason_style": "info",
        }
    elif approved_focus.get("task_id"):
        hint = {
            "stage": "approved_waiting_claim",
            "cli_focus_stage": "approved_waiting_claim",
            "focus_task": approved_focus,
            "status_label": "已放行待认领",
            "chain_reason": "链路: 监督者已放行该链路项，等待 API-A 自主执行面认领",
            "activity_text": "执行流: 监督者已放行链路项，等待 API-A 自主执行面认领",
            "reason_style": "warn",
        }
    elif deferred_governance:
        hint = {
            "stage": "governance_waiting",
            "cli_focus_stage": "idle",
            "focus_task": {},
            "status_label": "待命拉单",
            "chain_reason": "链路: 当前学习链路项大多仍停留在 API-B 治理段并被延后，尚未进入 API-A 待拉取段",
            "activity_text": "执行流: 等待 API-B 重新放行、重排或补充证据",
            "reason_style": "warn",
        }
    elif creativity_governance:
        hint = {
            "stage": "governance_waiting",
            "cli_focus_stage": "idle",
            "focus_task": {},
            "status_label": "待命拉单",
            "chain_reason": "链路: 当前自主链路项仍停留在 API-B 治理段，尚未进入 API-A 待拉取段",
            "activity_text": "执行流: 等待 API-B 审核、放行或重新排序链路项",
            "reason_style": "info",
        }
    elif chain_focus_cards:
        hint = {
            "stage": "api_b_or_mem_focus",
            "cli_focus_stage": "idle",
            "focus_task": {},
            "status_label": "待命拉单",
            "chain_reason": "链路: 当前没有新的 API-A 可执行链路项；API-B 正在判断、回收写回或推进下一轮再读取",
            "activity_text": "执行流: 等待监督者放行链路项或等待下一轮拉单",
            "reason_style": "info",
        }
    if stage_label:
        hint["status_label"] = stage_label
    if stage_reason:
        hint["chain_reason"] = stage_reason
    if stage_activity:
        hint["activity_text"] = stage_activity
    if stage_style:
        hint["reason_style"] = stage_style
    if hint["focus_task"] and not hint["focus_task"].get("task_id") and counts.get("api_a_ready"):
        hint["focus_task"] = dict(api_a_ready_items[0]) if api_a_ready_items else {}
    return hint


def resolve_autonomous_panel_focus_task(
    supervisor_state: Dict[str, Any],
    current_task: Dict[str, Any] | None,
) -> Dict[str, Any]:
    current = current_task or {}
    if current.get("task_id"):
        return current
    api_a_execution = supervisor_api_a_execution_hint(supervisor_state)
    hinted_focus = dict(api_a_execution.get("focus_task") or {})
    hinted_stage = str(
        api_a_execution.get("cli_focus_stage")
        or api_a_execution.get("stage")
        or ""
    ).strip()
    if hinted_focus.get("task_id"):
        hinted_focus["_supervisor_stage"] = hinted_stage
        return hinted_focus
    for task in observation_group_items(supervisor_state, "api_a_ready"):
        task_status = str(task.get("status") or "").strip().lower()
        if task_status in {"approved", "retry"} and task.get("task_id"):
            return task
    return {}


def resolve_autonomous_panel_focus_stage(
    focus_task: Dict[str, Any],
    *,
    current_task: Dict[str, Any] | None,
    agent_running: bool,
    last_agent_turn_result: Dict[str, Any] | None,
) -> str:
    current = current_task or {}
    current_task_id = str(current.get("task_id") or "").strip()
    focus_task_id = str(focus_task.get("task_id") or "").strip()
    if current_task_id and focus_task_id and current_task_id == focus_task_id:
        if agent_running:
            return "claimed_running"
        if last_agent_turn_result is not None:
            return "claimed_waiting_writeback"
        return "claimed_waiting_start"
    hinted_stage = str(focus_task.get("_supervisor_stage") or "").strip()
    if hinted_stage == "approved_waiting_claim":
        return "approved_waiting_claim"
    if hinted_stage == "running_autonomous_task":
        return "running_elsewhere"
    task_status = str(focus_task.get("status") or "").strip().lower()
    if task_status in {"approved", "retry"}:
        return "approved_waiting_claim"
    if task_status == "running":
        return "running_elsewhere"
    return "idle"


def resolve_supervisor_stage_descriptor(
    supervisor_state: Dict[str, Any],
    focus_stage: str,
) -> Dict[str, str]:
    api_a_execution = supervisor_api_a_execution_hint(supervisor_state)
    hinted_stage = str(
        api_a_execution.get("cli_focus_stage")
        or api_a_execution.get("stage")
        or ""
    ).strip()
    if not hinted_stage or hinted_stage != focus_stage:
        return {}
    return {
        "status_label": str(api_a_execution.get("status_label") or "").strip(),
        "chain_reason": str(api_a_execution.get("chain_reason") or "").strip(),
        "activity_text": str(api_a_execution.get("activity_text") or "").strip(),
        "reason_style": str(api_a_execution.get("reason_style") or "").strip().lower(),
    }


def resolve_autonomous_no_task_reason(supervisor_state: Dict[str, Any]) -> tuple[str, str]:
    api_a_execution = supervisor_api_a_execution_hint(supervisor_state)
    hinted_reason = str(api_a_execution.get("chain_reason") or "").strip()
    hinted_style = str(api_a_execution.get("reason_style") or "").strip().lower()
    if hinted_reason and str(api_a_execution.get("stage") or "").strip() not in {
        "",
        "approved_waiting_claim",
        "running_autonomous_task",
    }:
        style = {
            "warn": "class:auto-panel-warn",
            "info": "class:auto-panel-info",
            "good": "class:auto-panel-good",
            "dim": "class:auto-panel-dim",
        }.get(hinted_style, "class:auto-panel-dim")
        return (style, hinted_reason)

    ready_items = observation_group_items(supervisor_state, "api_a_ready")
    if ready_items:
        approved = [
            task for task in ready_items
            if str(task.get("status") or "").strip().lower() in {"approved", "retry"}
        ]
        if approved:
            return (
                "class:auto-panel-warn",
                "链路: 监督者已放行链路项，等待 API-A 自主执行面认领",
            )

    creativity_governance = [
        task
        for task in observation_group_items(supervisor_state, "api_b_backlog")
        if _is_creativity_observation_task(task)
    ]
    if creativity_governance:
        deferred = [
            task
            for task in creativity_governance
            if str(task.get("status") or "").strip().lower() == "deferred"
        ]
        if deferred:
            return (
                "class:auto-panel-warn",
                "链路: 当前学习链路项大多仍停留在 API-B 治理段并被延后，尚未进入 API-A 待拉取段",
            )
        return (
            "class:auto-panel-info",
            "链路: 当前自主链路项仍停留在 API-B 治理段，尚未进入 API-A 待拉取段",
        )

    stage_projections = observation_loop_stage_projections(supervisor_state)
    if any(
        str(card.get("observation_role") or "").strip()
        in {"api_b_judgement", "mem_writeback", "api_b_reread"}
        and str(card.get("status") or "").strip().lower() in {"active", "ready"}
        for card in stage_projections
    ):
        return (
            "class:auto-panel-info",
            "链路: 当前没有新的 API-A 可执行链路项；API-B 正在判断、回收写回或推进下一轮再读取",
        )

    other_execution = observation_loop_stage_projection(
        supervisor_state,
        "api_b_judgement",
        "mem_writeback",
        "api_b_reread",
    )
    if other_execution:
        return (
            "class:auto-panel-info",
            "链路: 当前没有新的 API-A 可执行链路项；闭环当前焦点仍在 API-B 或 Mem 侧",
        )

    return (
        "class:auto-panel-dim",
        "链路: 当前没有已批准的 API-A 可执行链路项",
    )


def format_supervisor_status_snapshot(state: Dict[str, Any]) -> list[str]:
    lines: list[str] = []
    observation = dict(state.get("autonomous_observation") or {})
    metrics = dict(observation.get("metrics") or {})
    by_path = dict(metrics.get("by_path") or {})
    governance = dict(metrics.get("governance") or {})
    board = dict(observation.get("board") or {})
    chain = dict(observation.get("chain") or {})
    focus = dict(board.get("primary_focus") or {})
    stage_projections = observation_loop_stage_projections(state)
    chain_segments = [
        dict(item)
        for item in list(chain.get("segments") or [])
        if isinstance(item, dict)
    ]
    timeline = list(state.get("timeline") or [])
    event_label_map = {
        "task_decided": "链路裁决",
        "tasks_reviewed": "批量复核",
        "tasks_planned": "链路规划",
        "supervisor_activity": "监督活动",
    }

    lines.append(f"场景: {state.get('scene', 'unknown')} — {state.get('title', '')}")
    lines.append(
        "链路统计: "
        f"learning={by_path.get('learning', 0)}, "
        f"maintenance={by_path.get('maintenance', 0)}, "
        f"evolution={by_path.get('evolution', 0)}, "
        f"running={metrics.get('running_count', 0)}"
    )
    lines.append(
        "治理统计: "
        f"direct={governance.get('direct_lm_actions', 0)}, "
        f"shadow={governance.get('shadow_recommendations', 0)}, "
        f"priority_updates={governance.get('priority_updates', 0)}"
    )

    if stage_projections:
        primary = stage_projections[0]
        lines.append(
            "闭环焦点: "
            f"{primary.get('title', 'unknown')} "
            f"({primary.get('display_status') or primary.get('status') or 'unknown'})"
        )
    elif focus:
        lines.append(
            "闭环焦点: "
            f"{focus.get('title', 'unknown')} "
            f"({focus.get('status') or 'unknown'})"
        )

    if chain_segments:
        segment_parts: list[str] = []
        for segment in chain_segments[:4]:
            label = str(segment.get("label") or segment.get("owner") or "?").strip() or "?"
            count = int(segment.get("count") or len(list(segment.get("items") or [])) or 0)
            segment_parts.append(f"{label}={count}")
        lines.append("链路分段: " + ", ".join(segment_parts))

    execution_card = observation_loop_stage_projection(
        state,
        "api_a_execution",
        "mem_writeback",
    )
    if execution_card:
        lines.append(
            f"执行焦点: {execution_card.get('title', 'unknown')} "
            f"({execution_card.get('display_status') or execution_card.get('status') or '链路项'})"
        )

    if timeline:
        latest = timeline[0]
        latest_label = str(latest.get("event_type", latest.get("source", "event")) or "event").strip()
        lines.append(
            f"最近监督/事件: {event_label_map.get(latest_label, latest_label)} — "
            f"{str(latest.get('summary') or latest.get('title') or '')[:120]}"
        )
    return lines

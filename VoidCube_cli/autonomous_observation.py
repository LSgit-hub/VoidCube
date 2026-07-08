from __future__ import annotations

from typing import Any, Dict


_SCENE_LABELS = {
    "idle": "静置",
    "planning": "治理安排",
    "drive": "内生判断",
    "memory": "记忆整理",
    "maintenance": "连续性维护",
    "handoff": "自主交接",
}


def _display_text(value: Any, fallback: str = "未命名") -> str:
    text = str(value or "").strip()
    return text or fallback


def _scene_label(value: Any) -> str:
    text = str(value or "").strip().lower()
    return _SCENE_LABELS.get(text, _display_text(value, "未识别"))


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
    stage_cards = [
        dict(stage_card)
        for stage_card in list(loop.get("stage_cards") or [])
        if isinstance(stage_card, dict)
    ]
    if stage_cards:
        for stage_card in stage_cards:
            focus_task = dict(stage_card.get("focus_task") or {})
            projected.append(
                {
                    **focus_task,
                    "title": str(
                        stage_card.get("title")
                        or stage_card.get("observation_stage_label")
                        or "阶段"
                    ),
                    "status": str(stage_card.get("status") or focus_task.get("status") or "idle"),
                    "display_status": str(
                        stage_card.get("display_status")
                        or stage_card.get("status_label")
                        or stage_card.get("title")
                        or "等待中"
                    ),
                    "summary": str(
                        stage_card.get("summary")
                        or stage_card.get("chain_reason")
                        or stage_card.get("activity_text")
                        or focus_task.get("summary")
                        or ""
                    ),
                    "observation_role": str(
                        stage_card.get("stage_key")
                        or stage_card.get("observation_role")
                        or ""
                    ),
                }
            )
        return projected
    return []


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
    normalized_key = str(stage_key or "").strip()
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
        "status_label": "治理段观察中",
        "chain_reason": "链路: 当前没有已批准的 API-A 可执行链路项",
        "activity_text": "执行流: API-B 判断、重排或再读取后再交给 API-A",
        "reason_style": "dim",
    }
    approved_focus = (
        dict(focus_task)
        if focus_status == "approved"
        else (dict(approved_api_a[0]) if approved_api_a else {})
    )
    if focus_task.get("task_id") and focus_status == "running":
        hint = {
            "stage": "running_on_other_api_a",
            "cli_focus_stage": "running_on_other_api_a",
            "focus_task": dict(focus_task),
            "status_label": "他处执行中",
            "chain_reason": "链路: 该链路项已被其他 API-A 自主执行面认领",
            "activity_text": "执行流: 链路项正在其他 API-A 自主执行面中运行",
            "reason_style": "info",
        }
    elif approved_focus.get("task_id"):
        hint = {
            "stage": "waiting_api_a_claim",
            "cli_focus_stage": "waiting_api_a_claim",
            "focus_task": approved_focus,
            "status_label": "API-A 可认领",
            "chain_reason": "链路: API-B 已放行，可由 API-A 自主执行面认领",
            "activity_text": "执行流: API-A 认领后执行，结果写回 Mem",
            "reason_style": "warn",
        }
    elif deferred_governance:
        hint = {
            "stage": "governance_waiting",
            "cli_focus_stage": "idle",
            "focus_task": {},
            "status_label": "治理段观察中",
            "chain_reason": "链路: 当前学习链路项仍由 API-B 判断",
            "activity_text": "执行流: API-B 补判断后再决定是否交给 API-A",
            "reason_style": "warn",
        }
    elif creativity_governance:
        hint = {
            "stage": "governance_waiting",
            "cli_focus_stage": "idle",
            "focus_task": {},
            "status_label": "治理段观察中",
            "chain_reason": "链路: 当前自主链路项仍由 API-B 判断",
            "activity_text": "执行流: API-B 审核、放行或重排后再交给 API-A",
            "reason_style": "info",
        }
    elif chain_focus_cards:
        hint = {
            "stage": "api_b_or_mem_focus",
            "cli_focus_stage": "idle",
            "focus_task": {},
            "status_label": "治理段观察中",
            "chain_reason": "链路: 当前没有新的 API-A 可执行链路项；API-B 正在判断、回收写回或推进下一轮再读取",
            "activity_text": "执行流: API-B 判断、重排或再读取后再交给 API-A",
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
            return "local_claimed_active"
        if last_agent_turn_result is not None:
            return "local_claimed_waiting_writeback"
        return "local_claimed_waiting_first_turn"
    hinted_stage = str(focus_task.get("_supervisor_stage") or "").strip()
    if hinted_stage == "waiting_api_a_claim":
        return "waiting_api_a_claim"
    if hinted_stage == "running_on_other_api_a":
        return "running_on_other_api_a"
    task_status = str(focus_task.get("status") or "").strip().lower()
    if task_status in {"approved", "retry"}:
        return "waiting_api_a_claim"
    if task_status == "running":
        return "running_on_other_api_a"
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
        "waiting_api_a_claim",
        "running_on_other_api_a",
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
                "链路: API-B 已放行链路项，可由 API-A 自主执行面认领",
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
                "链路: 当前学习链路项仍由 API-B 判断",
            )
        return (
            "class:auto-panel-info",
            "链路: 当前自主链路项仍由 API-B 判断",
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
    chain_projection = dict(metrics.get("chain_projection") or {})
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
        "tasks_reviewed": "API-B 复核记录",
        "tasks_planned": "链路规划",
        "supervisor_activity": "监督活动",
        "execution_handoff_started": "自主交接",
        "execution_handoff_completed": "自主交接完成",
        "execution_handoff_failed": "自主交接失败",
        "execution_handoff_retry": "自主交接重试",
    }

    lines.append(f"场景: {_scene_label(state.get('scene'))} — {_display_text(state.get('title'), '自主链路观测')}")
    lines.append(
        "闭环统计: "
        f"API-B 判断在途={chain_projection.get('governance_backlog', 0)}, "
        f"API-A 执行中={chain_projection.get('api_a_running', 0)}, "
        f"API-A 可认领={chain_projection.get('api_a_ready', 0)}, "
        f"候选={chain_projection.get('candidate_signals', 0)}, "
        f"回流={chain_projection.get('writeback_history', 0)}"
    )
    lines.append(
        "治理统计: "
        f"裁定={governance.get('review_actions', 0)}, "
        f"建议={governance.get('followup_suggestions', 0)}, "
        f"重排={governance.get('priority_adjustments', 0)}"
    )

    if focus:
        lines.append(
            "闭环焦点: "
            f"{_display_text(focus.get('title'))} "
            f"({_display_text(focus.get('status'), '等待中')})"
        )
    elif stage_projections:
        primary = stage_projections[0]
        lines.append(
            "闭环焦点: "
            f"{_display_text(primary.get('title'))} "
            f"({_display_text(primary.get('display_status') or primary.get('status'), '等待中')})"
        )

    if chain_segments:
        segment_parts: list[str] = []
        for segment in chain_segments[:4]:
            label = str(segment.get("label") or "?").strip() or "?"
            count = int(segment.get("count") or len(list(segment.get("items") or [])) or 0)
            segment_parts.append(f"{label}={count}")
        lines.append("闭环分段: " + ", ".join(segment_parts))

    execution_card = observation_loop_stage_projection(
        state,
        "api_a_execution",
        "mem_writeback",
    )
    if execution_card:
        lines.append(
            f"执行焦点: {_display_text(execution_card.get('title'))} "
            f"({_display_text(execution_card.get('display_status') or execution_card.get('status'), '链路项')})"
        )

    if timeline:
        latest = timeline[0]
        latest_label = str(latest.get("event_type", latest.get("source", "event")) or "event").strip()
        lines.append(
            f"最近监督/事件: {event_label_map.get(latest_label, latest_label)} — "
            f"{str(latest.get('summary') or latest.get('title') or '')[:120]}"
        )
    return lines

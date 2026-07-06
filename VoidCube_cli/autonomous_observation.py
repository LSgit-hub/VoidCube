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


def observation_current_cards(state: Dict[str, Any]) -> list[Dict[str, Any]]:
    board = observation_board(state)
    return [
        dict(item)
        for item in list(board.get("current_cards") or [])
        if isinstance(item, dict)
    ]


def observation_current_card(
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
    for item in observation_current_cards(state):
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


def supervisor_api_a_execution_hint(supervisor_state: Dict[str, Any]) -> Dict[str, Any]:
    observation = dict(supervisor_state.get("autonomous_observation") or {})
    counts = dict(observation.get("counts") or {})
    api_a_stage = observation_loop_stage(supervisor_state, "api_a_execution")
    stage_label = str(api_a_stage.get("status_label") or "").strip()
    stage_reason = str(api_a_stage.get("chain_reason") or "").strip()
    stage_activity = str(api_a_stage.get("activity_text") or "").strip()
    stage_style = str(api_a_stage.get("reason_style") or "").strip().lower()
    api_a_pending = observation_group_items(supervisor_state, "api_a_ready")
    focus_task = dict(api_a_stage.get("focus_task") or {})
    focus_status = str(focus_task.get("status") or "").strip().lower()
    approved_api_a = [
        task
        for task in api_a_pending
        if str(task.get("status") or "").strip().lower() == "approved"
    ]
    deferred_api_a = [
        task
        for task in api_a_pending
        if str(task.get("status") or "").strip().lower() == "deferred"
    ]
    current_cards = observation_current_cards(supervisor_state)
    chain_focus_cards = [
        dict(card)
        for card in current_cards
        if str(card.get("observation_role") or "").strip()
        in {"api_b_judgement", "mem_writeback", "api_b_reread"}
        and str(card.get("status") or "").strip().lower() in {"active", "ready"}
    ]
    hint: Dict[str, Any] = {
        "stage": "idle",
        "cli_focus_stage": "idle",
        "focus_task": {},
        "status_label": "待命拉单",
        "chain_reason": "链路: 当前没有已批准的 API-A 可执行任务",
        "activity_text": "执行流: 等待监督者放行任务或等待下一轮拉单",
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
            "status_label": "执行中",
            "chain_reason": "链路: 某个 API-A 自主执行面已认领该任务并正在运行",
            "activity_text": "执行流: 任务正在 API-A 自主执行面中运行",
            "reason_style": "info",
        }
    elif approved_focus.get("task_id"):
        hint = {
            "stage": "approved_waiting_claim",
            "cli_focus_stage": "approved_waiting_claim",
            "focus_task": approved_focus,
            "status_label": "已放行待认领",
            "chain_reason": "链路: 监督者已放行该任务，等待 API-A 自主执行面认领",
            "activity_text": "执行流: 监督者已放行任务，等待 API-A 自主执行面认领",
            "reason_style": "warn",
        }
    elif deferred_api_a and not approved_api_a:
        hint = {
            "stage": "deferred_only",
            "cli_focus_stage": "idle",
            "focus_task": {},
            "status_label": "待命拉单",
            "chain_reason": "链路: 当前学习任务大多被监督者延后，当前没有已批准的 API-A 可执行任务",
            "activity_text": "执行流: 等待监督者放行任务或等待下一轮拉单",
            "reason_style": "warn",
        }
    elif deferred_api_a:
        hint = {
            "stage": "mixed_without_approval",
            "cli_focus_stage": "idle",
            "focus_task": {},
            "status_label": "待命拉单",
            "chain_reason": "链路: 当前没有已批准的 API-A 可执行任务；最近自主任务多处于 deferred/待观察",
            "activity_text": "执行流: 等待监督者放行任务或等待下一轮拉单",
            "reason_style": "dim",
        }
    elif chain_focus_cards:
        hint = {
            "stage": "api_b_or_mem_focus",
            "cli_focus_stage": "idle",
            "focus_task": {},
            "status_label": "待命拉单",
            "chain_reason": "链路: 当前没有新的 API-A 可执行任务；API-B 正在判断、回收写回或推进下一轮再读取",
            "activity_text": "执行流: 等待监督者放行任务或等待下一轮拉单",
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
        hint["focus_task"] = dict(api_a_pending[0]) if api_a_pending else {}
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
        if task_status in {"approved", "running"} and task.get("task_id"):
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
    if task_status == "approved":
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

    learning_tasks = observation_group_items(supervisor_state, "api_a_ready")
    if learning_tasks:
        approved = [
            task for task in learning_tasks
            if str(task.get("status") or "").strip().lower() == "approved"
        ]
        deferred = [
            task for task in learning_tasks
            if str(task.get("status") or "").strip().lower() == "deferred"
        ]
        if deferred and not approved:
            return (
                "class:auto-panel-warn",
                "链路: 当前学习任务大多被监督者延后，当前没有已批准的 API-A 可执行任务",
            )
        if deferred:
            return (
                "class:auto-panel-dim",
                "链路: 当前没有已批准的 API-A 可执行任务；最近自主任务多处于 deferred/待观察",
            )

    current_cards = observation_current_cards(supervisor_state)
    if any(
        str(card.get("observation_role") or "").strip()
        in {"api_b_judgement", "mem_writeback", "api_b_reread"}
        and str(card.get("status") or "").strip().lower() in {"active", "ready"}
        for card in current_cards
    ):
        return (
            "class:auto-panel-info",
            "链路: 当前没有新的 API-A 可执行任务；API-B 正在判断、回收写回或推进下一轮再读取",
        )

    other_execution = observation_current_card(
        supervisor_state,
        "api_b_judgement",
        "mem_writeback",
        "api_b_reread",
    )
    if other_execution:
        return (
            "class:auto-panel-info",
            "链路: 当前没有新的 API-A 可执行任务；闭环当前焦点仍在 API-B 或 Mem 侧",
        )

    return (
        "class:auto-panel-dim",
        "链路: 当前没有已批准的 API-A 可执行任务",
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
    current_cards = observation_current_cards(state)
    chain_segments = [
        dict(item)
        for item in list(chain.get("segments") or [])
        if isinstance(item, dict)
    ]
    timeline = list(state.get("timeline") or [])

    lines.append(f"Scene: {state.get('scene', 'unknown')} — {state.get('title', '')}")
    lines.append(
        "Tasks: "
        f"learning={by_path.get('learning', 0)}, "
        f"maintenance={by_path.get('maintenance', 0)}, "
        f"evolution={by_path.get('evolution', 0)}, "
        f"running={metrics.get('running_count', 0)}"
    )
    lines.append(
        "Governance: "
        f"direct={governance.get('direct_lm_actions', 0)}, "
        f"shadow={governance.get('shadow_recommendations', 0)}, "
        f"priority_updates={governance.get('priority_updates', 0)}"
    )

    if current_cards:
        primary = current_cards[0]
        lines.append(
            "Autonomous Focus: "
            f"{primary.get('title', 'unknown')} "
            f"({primary.get('display_status') or primary.get('status') or 'unknown'})"
        )
    elif focus:
        lines.append(
            "Autonomous Focus: "
            f"{focus.get('title', 'unknown')} "
            f"({focus.get('status') or 'unknown'})"
        )

    if chain_segments:
        segment_parts: list[str] = []
        for segment in chain_segments[:4]:
            label = str(segment.get("label") or segment.get("owner") or "?").strip() or "?"
            count = int(segment.get("count") or len(list(segment.get("items") or [])) or 0)
            segment_parts.append(f"{label}={count}")
        lines.append("Chain Segments: " + ", ".join(segment_parts))

    execution_card = observation_current_card(
        state,
        "api_a_execution",
        "mem_writeback",
    )
    if execution_card:
        lines.append(
            f"Active Execution: {execution_card.get('title', 'unknown')} "
            f"({execution_card.get('display_status') or execution_card.get('status') or 'task'})"
        )

    if timeline:
        latest = timeline[0]
        lines.append(
            f"Latest Review/Event: {latest.get('event_type', latest.get('source', 'event'))} — "
            f"{str(latest.get('summary') or latest.get('title') or '')[:120]}"
        )
    return lines

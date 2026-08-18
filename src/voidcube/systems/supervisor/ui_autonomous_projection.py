"""Pure autonomous-chain observation assembly for the Supervisor web UI."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from .ui_observation_projection import (
    build_observation_card,
    build_observation_group,
    chain_projection_order_key,
    is_api_a_execution_lane_task,
    is_api_a_lane_family_task,
    loop_stage_status_label,
    memory_maintenance_handoff_status,
    observation_display_status,
    observation_role_tag,
    observation_stage_subtitle,
    observation_status_value,
    project_observation_rail_entry,
    project_observation_stage_card,
)
from .ui_trace_projection import project_chain_segment_activity
def project_autonomous_observation(
    all_tasks: List[Dict[str, Any]],
    *,
    drive_candidates: List[Dict[str, Any]],
    history_tasks: Optional[List[Dict[str, Any]]] = None,
    timeline: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    api_b_judgement_statuses = {
        "planned",
        "deferred",
        "paused",
        "awaiting_review",
    }
    api_b_local_statuses = {
        "planned",
        "deferred",
        "approved",
        "running",
        "awaiting_user_consent",
        "paused",
        "awaiting_review",
        "retry",
    }
    api_a_lane_family_tasks = [
        task for task in all_tasks if is_api_a_lane_family_task(task)
    ]
    supervisor_tasks = [
        task for task in all_tasks if not is_api_a_lane_family_task(task)
    ]

    api_a_lane_family_sorted = sorted(api_a_lane_family_tasks, key=chain_projection_order_key)
    supervisor_sorted = sorted(
        [
            task
            for task in supervisor_tasks
            if observation_status_value(task) in api_b_local_statuses
        ],
        key=chain_projection_order_key,
    )
    api_a_lane_source = [
        task for task in api_a_lane_family_sorted if is_api_a_execution_lane_task(task)
    ]
    api_a_running_source = [
        task
        for task in api_a_lane_source
        if observation_status_value(task) == "running"
    ]
    api_a_pre_handoff_source = [
        task
        for task in api_a_lane_family_sorted
        if observation_status_value(task) in api_b_judgement_statuses
    ]
    api_b_judgement_source = sorted(
        [*supervisor_sorted, *api_a_pre_handoff_source],
        key=chain_projection_order_key,
    )

    def pick_active(rows: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        running = [
            row for row in rows
            if observation_status_value(row) == "running"
        ]
        if running:
            return sorted(running, key=chain_projection_order_key)[0]
        approved = [
            row for row in rows
            if observation_status_value(row) == "approved"
        ]
        if approved:
            return sorted(approved, key=chain_projection_order_key)[0]
        return None

    api_b_focus_task = pick_active(supervisor_sorted)
    api_a_running_task = pick_active(api_a_running_source)

    api_b_judgement_cards = [
        build_observation_card(
            task,
            lane="supervisor",
            observation_role="observed_task",
        )
        for task in api_b_judgement_source
    ]
    api_b_judgement_cards = [
        task for task in api_b_judgement_cards if isinstance(task, dict)
    ]
    api_a_lane_items = [
        build_observation_card(
            task,
            lane="agent",
            observation_role="observed_task",
        )
        for task in api_a_lane_source
    ]
    api_a_lane_items = [
        task for task in api_a_lane_items if isinstance(task, dict)
    ]
    api_a_handoff_items = [
        task
        for task in api_a_lane_items
        if str(task.get("status") or "").strip().lower() in {"approved", "retry"}
    ]
    api_a_pre_handoff_cards = [
        card for card in api_b_judgement_cards if is_api_a_lane_family_task(card)
    ]
    terminal_history_tasks = [
        task
        for task in (history_tasks or all_tasks)
        if str(task.get("status") or "").strip().lower()
        in {"completed", "failed", "cancelled"}
    ]

    seen_keys = {
        str(task.get("metadata", {}).get("endogenous_drive_key") or "").strip()
        for task in [*api_b_judgement_cards, *api_a_lane_items, *terminal_history_tasks]
        if isinstance(task, dict)
    }
    seen_titles = {
        str(task.get("title") or "").strip()
        for task in [*api_b_judgement_cards, *api_a_lane_items]
        if isinstance(task, dict)
    }
    seen_task_ids = {
        str(task.get("task_id") or "").strip()
        for task in terminal_history_tasks
        if isinstance(task, dict)
    }
    candidates: List[Dict[str, Any]] = []
    for candidate in drive_candidates:
        if not isinstance(candidate, dict):
            continue
        candidate_key = str(
            candidate.get("metadata", {}).get("endogenous_drive_key")
            or candidate.get("stable_key")
            or ""
        ).strip()
        candidate_title = str(candidate.get("title") or "").strip()
        candidate_task_id = str(candidate.get("task_id") or "").strip()
        if candidate_key and candidate_key in seen_keys:
            continue
        if candidate_task_id and candidate_task_id in seen_task_ids:
            continue
        if candidate_title and candidate_title in seen_titles:
            continue
        candidate_card = build_observation_card(
            candidate,
            lane="supervisor",
            display_status="候选形成",
            status="candidate",
            observation_role="candidate",
        )
        if candidate_card is not None:
            candidates.append(candidate_card)
        if candidate_key:
            seen_keys.add(candidate_key)
        if candidate_title:
            seen_titles.add(candidate_title)

    api_a_handoff_focus = api_a_handoff_items[0] if api_a_handoff_items else None
    deferred_api_a_pre_handoff = [
        task
        for task in api_a_pre_handoff_cards
        if str(task.get("status") or "").strip().lower() == "deferred"
    ]
    completed_tasks = [
        task
        for task in (history_tasks or all_tasks)
        if str(task.get("status") or "").strip().lower() in {"completed", "failed"}
    ]
    recent_writebacks = [
        build_autonomous_writeback_summary(task)
        for task in completed_tasks[:3]
    ]
    recent_writeback_cards = [
        build_observation_card(
            item,
            lane="mem",
            observation_role=str(
                item.get("observation_role") or "mem_writeback"
            ).strip(),
        )
        for item in recent_writebacks
    ]
    recent_writeback_cards = [
        item for item in recent_writeback_cards if isinstance(item, dict)
    ]

    if candidates:
        api_b_summary = f"API-B 正在判断 {len(candidates)} 个新候选"
        api_b_status = "active"
    elif api_b_judgement_cards:
        api_b_summary = f"API-B 正在判断 {len(api_b_judgement_cards)} 个链路项"
        api_b_status = "active"
    else:
        api_b_summary = "当前没有新的 API-B 动作"
        api_b_status = "idle"

    if api_a_running_task:
        api_a_status = "active"
        api_a_summary = f"{str(api_a_running_task.get('title') or '自主链路项').strip()} 正在 API-A 执行"
    elif api_a_handoff_items:
        api_a_status = "ready"
        api_a_summary = f"API-B 已转交 {len(api_a_handoff_items)} 个链路项，等待 API-A 接手"
    elif api_a_pre_handoff_cards:
        api_a_status = "idle"
        api_a_summary = f"{len(api_a_pre_handoff_cards)} 个链路项仍由 API-B 判断"
    else:
        api_a_status = "idle"
        api_a_summary = "当前没有 API-A 自主执行项"

    if recent_writebacks:
        writeback_status = "ready"
        writeback_summary = f"{recent_writebacks[0]['title']} 的执行结果已回流到 Mem"
    else:
        writeback_status = "idle"
        writeback_summary = "暂无新的 Mem 回流"

    if recent_writebacks and (candidates or api_b_judgement_cards):
        reread_status = "active"
        reread_summary = "API-B 正结合最新 Mem 回流与在途链路项推进下一轮判断"
    elif recent_writebacks:
        reread_status = "ready"
        reread_summary = "最新 Mem 回流已可供 API-B 再读取"
    else:
        reread_status = "idle"
        reread_summary = "暂无可再读回流"

    api_a_stage_label = "未进入"
    api_a_chain_reason = "链路: 当前没有 API-A 自主执行项"
    api_a_activity_text = "执行流: 只观察 API-A 对 API-B 可见的状态"
    api_a_reason_style = "dim"
    if api_a_running_task:
        api_a_stage_label = "执行中"
        api_a_chain_reason = "链路: API-A 正在执行并回报进展"
        api_a_activity_text = "执行流: 完成后写回 Mem"
        api_a_reason_style = "info"
    elif api_a_handoff_items:
        api_a_stage_label = "待接手"
        api_a_chain_reason = "链路: API-B 已转交，可由 API-A 接手"
        api_a_activity_text = "执行流: API-A 接手后执行，结果写回 Mem"
        api_a_reason_style = "warn"
    elif deferred_api_a_pre_handoff:
        api_a_chain_reason = "链路: 当前学习链路项仍由 API-B 判断"
        api_a_activity_text = "执行流: API-B 先补判断，再决定是否交给 API-A"
        api_a_reason_style = "warn"
    elif api_a_pre_handoff_cards:
        api_a_chain_reason = "链路: 当前自主链路项仍由 API-B 判断"
        api_a_activity_text = "执行流: API-B 决定是否交给 API-A"
        api_a_reason_style = "info"
    elif recent_writebacks or candidates or api_b_judgement_cards:
        api_a_chain_reason = "链路: API-B 正结合候选与回流推进下一轮"
        api_a_reason_style = "info"

    api_b_current = build_observation_card(
        api_b_focus_task
        or (api_b_judgement_cards[0] if api_b_judgement_cards else None)
        or (candidates[0] if candidates else None)
        or {"title": "API-B 判断"},
        lane="supervisor",
        display_status=loop_stage_status_label(api_b_status),
        status=api_b_status,
        summary_override=api_b_summary,
        observation_role="api_b_judgement",
        title_override=(
            str((api_b_focus_task or {}).get("title") or "").strip()
            or str((api_b_judgement_cards[0] if api_b_judgement_cards else {}).get("title") or "").strip()
            or str((candidates[0] if candidates else {}).get("title") or "").strip()
            or "API-B 判断"
        ),
    )
    api_a_current = build_observation_card(
        api_a_running_task
        or api_a_handoff_focus
        or {"title": "API-A 自主执行"},
        lane="agent",
        display_status=loop_stage_status_label(api_a_status),
        status=api_a_status,
        summary_override=api_a_summary,
        observation_role="api_a_execution",
        title_override=(
            str((api_a_running_task or {}).get("title") or "").strip()
            or str((api_a_handoff_focus or {}).get("title") or "").strip()
            or "API-A 自主执行"
        ),
    )
    mem_current = build_observation_card(
        (recent_writeback_cards[0] if recent_writeback_cards else None)
        or {"title": "Mem 写回"},
        lane="mem",
        display_status=loop_stage_status_label(writeback_status),
        status=writeback_status,
        summary_override=writeback_summary,
        observation_role="mem_writeback",
        title_override=(
            str((recent_writeback_cards[0] if recent_writeback_cards else {}).get("title") or "").strip()
            or "Mem 写回"
        ),
    )
    reread_card = build_observation_card(
        {"title": "API-B 再读取", "summary": reread_summary},
        lane="supervisor",
        display_status=loop_stage_status_label(reread_status),
        status=reread_status,
        summary_override=reread_summary,
        observation_role="api_b_reread",
    )
    api_b_active_task = (
        build_observation_card(
            api_b_focus_task,
            lane="supervisor",
            observation_role="api_b_active_task",
        )
        if api_b_focus_task
        else None
    )
    api_a_active_task = (
        build_observation_card(
            api_a_running_task,
            lane="agent",
            observation_role="api_a_active_task",
        )
        if api_a_running_task
        else None
    )

    chain_segments = [
        build_observation_group(
            key="api_b_candidates",
            label="候选形成",
            empty_text="当前没有候选",
            items=candidates[:6],
            emphasis="candidate",
            source_label="API-B",
            stage_label="刚形成",
            summary="API-B 内生驱动刚形成的新候选，尚未进入治理闭环。",
            order=0,
            segment_kind="candidate_judgement",
            decor_cls="candidate",
            decor_icon="🪄",
            item_label="候选",
            event_label="动作",
            trace_label="回合",
            footer_label="查看候选最近状态",
            drill_label="查看候选详情",
            read_rule="这里只看刚形成的新候选。",
            next_step="API-B 会决定它们进入判断在途，或在本轮直接丢弃。",
        ),
        build_observation_group(
            key="api_b_judgement",
            label="API-B 判断在途",
            empty_text="当前没有 API-B 判断在途",
            items=api_b_judgement_cards[:6],
            emphasis="supervisor",
            source_label="API-B",
            stage_label="判断在途",
            summary="仍由 API-B 判断、补证、重排或延后的自主链路项。",
            order=1,
            segment_kind="api_b_judgement",
            decor_cls="supervisor",
            decor_icon="🧠",
            item_label="判断项",
            event_label="动作",
            trace_label="回合",
            footer_label="查看判断最近状态",
            drill_label="查看判断详情",
            read_rule="这里只看 API-B 正在判断的事。",
            next_step="API-B 判断通过后交给 API-A。",
        ),
        build_observation_group(
            key="api_a_handoff",
            label="API-B 已转交",
            empty_text="当前没有已转交待接手项",
            items=api_a_handoff_items[:6],
            emphasis="agent",
            source_label="API-A",
            stage_label="接手状态",
            summary="API-B 已转交，等待 API-A 接手的自主链路项。",
            order=2,
            segment_kind="api_a_handoff",
            decor_cls="agent",
            decor_icon="🤖",
            item_label="待接手项",
            event_label="动作",
            trace_label="回合",
            footer_label="查看执行最近状态",
            drill_label="查看执行详情",
            read_rule="这里只看 API-B 已转交、等待 API-A 接手的项；执行中看上方阶段。",
            next_step="API-A 接手后执行，结果回流到 Mem。",
        ),
        build_observation_group(
            key="mem_recent",
            label="写回回流",
            empty_text="尚未观察到新的 Mem 写回记录",
            items=recent_writeback_cards[:4],
            emphasis="mem",
            source_label="Mem",
            stage_label="写回回流",
            summary="最近返回到 Memory 侧的执行结果与维护受理状态。",
            order=3,
            segment_kind="mem_writeback",
            decor_cls="mem",
            decor_icon="💾",
            item_label="回流结果",
            event_label="动作",
            trace_label="回合",
            footer_label="查看回流最近状态",
            drill_label="查看回流详情",
            read_rule="这里只看回流结果。",
            next_step="这些回流结果会被 API-B 再读取，决定下一轮是否形成新候选。",
        ),
    ]
    chain_segments = project_chain_segment_activity(
        chain_segments=chain_segments,
        timeline=[
            dict(event)
            for event in list(timeline or [])
            if isinstance(event, dict)
        ],
        activity_items_by_key={
            "api_b_judgement": [
                item
                for item in (api_b_current, api_b_active_task, *api_b_judgement_cards)
                if isinstance(item, dict)
            ],
            "api_a_handoff": [
                item
                for item in (api_a_current, api_a_active_task, *api_a_handoff_items)
                if isinstance(item, dict)
            ],
            "api_b_candidates": [
                item
                for item in candidates
                if isinstance(item, dict)
            ],
            "mem_recent": [
                item
                for item in (mem_current, *recent_writeback_cards)
                if isinstance(item, dict)
            ],
        },
    )

    focus_card = next(
        (
            card
            for card in (api_b_current, api_a_current, mem_current, reread_card)
            if isinstance(card, dict)
            and str(card.get("status") or "").strip().lower() in {"active", "ready"}
        ),
        api_b_current,
    )
    focus_role = str((focus_card or {}).get("observation_role") or "").strip()
    board = {
        "headline": "API-B 主视角自主闭环总览",
        "summary": (
            "Web 小屋只看 API-B 判断、API-A 回报、Mem 回流与再读取；用户链路只作软感知。"
        ),
        "primary_focus": {
            "title": str((focus_card or {}).get("title") or "自主闭环当前落点").strip(),
            "status": str((focus_card or {}).get("display_status") or "等待中").strip(),
            "stage_status": str((focus_card or {}).get("status") or "idle").strip().lower(),
            "summary": str((focus_card or {}).get("summary") or "").strip(),
            "observation_role": str((focus_card or {}).get("observation_role") or "").strip(),
            "stage_key": str(
                (focus_card or {}).get("stage_key")
                or (focus_card or {}).get("observation_role")
                or ""
            ).strip(),
            "source_label": str((focus_card or {}).get("source_label") or "").strip(),
        },
    }

    loop_stages = [
        {
            "key": "api_b_judgement",
            "label": "API-B 判断",
            "observation_stage_label": "API-B 判断阶段",
            "source_label": "API-B",
            "lane": "supervisor",
            "observation_role": "api_b_judgement",
            "status": api_b_status,
            "rail_state": loop_stage_status_label(api_b_status),
            "rail_note": api_b_summary,
            "is_focus": focus_role == "api_b_judgement",
            "summary": api_b_summary,
            "read_rule": "这里看 API-B 这轮判断。",
            "transition_hint": "判断通过后交给 API-A 接手。",
            "focus_task": (
                api_b_active_task
                or (candidates[0] if candidates else None)
                or (
                    api_b_judgement_cards[0]
                    if api_b_judgement_cards
                    else None
                )
            ),
        },
        {
            "key": "api_a_execution",
            "label": "API-A 自主执行",
            "observation_stage_label": "API-A 接手 / 执行观测阶段",
            "source_label": "API-A",
            "lane": "agent",
            "observation_role": "api_a_execution",
            "status": api_a_status,
            "rail_state": api_a_stage_label,
            "rail_note": api_a_chain_reason,
            "is_focus": focus_role == "api_a_execution",
            "summary": api_a_summary,
            "status_label": api_a_stage_label,
            "chain_reason": api_a_chain_reason,
            "activity_text": api_a_activity_text,
            "reason_style": api_a_reason_style,
            "read_rule": "这里只看 API-A 对 API-B 可见的接手与执行状态。",
            "transition_hint": "执行完成后会把结果写回 Mem，形成回流证据。",
            "focus_task": api_a_active_task or api_a_handoff_focus,
        },
        {
            "key": "mem_writeback",
            "label": "Mem 写回",
            "observation_stage_label": "Mem 写回阶段",
            "source_label": "Mem",
            "lane": "mem",
            "observation_role": "mem_writeback",
            "status": writeback_status,
            "rail_state": loop_stage_status_label(writeback_status),
            "rail_note": writeback_summary,
            "is_focus": focus_role == "mem_writeback",
            "summary": writeback_summary,
            "read_rule": "这里看刚回流到 Mem 的结果。",
            "transition_hint": "这些回流结果会供下一轮 API-B 再读取。",
            "focus_task": recent_writeback_cards[0] if recent_writeback_cards else None,
        },
        {
            "key": "api_b_reread",
            "label": "API-B 再读取",
            "observation_stage_label": "API-B 再读取阶段",
            "source_label": "API-B",
            "lane": "supervisor",
            "observation_role": "api_b_reread",
            "status": reread_status,
            "rail_state": loop_stage_status_label(reread_status),
            "rail_note": reread_summary,
            "is_focus": focus_role == "api_b_reread",
            "summary": reread_summary,
            "read_rule": "这里看 API-B 再读取回流。",
            "transition_hint": "再读取后会回到候选形成，或在本轮收束闭环。",
            "focus_task": recent_writeback_cards[0] if recent_writeback_cards else None,
        },
    ]
    for stage in loop_stages:
        stage["card_subtitle"] = observation_stage_subtitle(stage)

    focus_stage_projection = next(
        (
            stage
            for stage in loop_stages
            if str(stage.get("observation_role") or "").strip() == focus_role
        ),
        None,
    )
    if isinstance(focus_stage_projection, dict):
        board["primary_focus"]["source_label"] = str(
            focus_stage_projection.get("source_label") or ""
        ).strip()

    loop_stage_cards = [
        project_observation_stage_card(stage)
        for stage in loop_stages
    ]
    rail_entries = [
        project_observation_rail_entry(stage)
        for stage in loop_stages
    ]
    boundary_note = (
        "自主链路闭环只展示 API-B 判断、API-A 自主执行、Mem 写回回流和 API-B 再读取；"
        "用户链路只作让路软感知，不展示聊天内容。"
    )

    return {
        "read_model_version": 13,
        "mode": {
            "label": "观测模式",
            "scope": "api_b_autonomous_chain_only",
            "status_text": "只读观测 API-B 与自主链路",
        },
        "runtime": {},
        "chain": {
            "headline": "自主闭环分段观察",
            "summary": "这里按候选形成、API-B 判断在途、API-A 接手与执行、Mem 回流来看这一条自主链路。",
            "segments": chain_segments,
        },
        "board": board,
        "loop": {
            "boundary": boundary_note,
            "rail_entries": rail_entries,
            "stage_cards": loop_stage_cards,
            "recent_writebacks": recent_writebacks,
        },
        "counts": {
            "candidates": len(candidates),
            "writebacks": len(recent_writebacks),
            "api_b_judgement": len(api_b_judgement_cards),
            "api_a_handoff": len(api_a_handoff_items),
            "api_a_running": len(api_a_running_source),
        },
    }

def build_autonomous_writeback_summary(
    task: Dict[str, Any],
) -> Dict[str, Any]:
    metadata = dict(task.get("metadata") or {})
    execution_result = dict(metadata.get("execution_result") or {})
    summary = (
        execution_result.get("outcome_summary")
        or execution_result.get("summary")
        or execution_result.get("final_response")
        or task.get("decision_reason")
        or task.get("summary")
        or ""
    )
    display_status = observation_display_status(task)
    observation_role = (
        "memory_maintenance_receipt"
        if memory_maintenance_handoff_status(task)
        else "mem_writeback"
    )
    return {
        "task_id": task.get("task_id"),
        "title": str(task.get("title") or "未命名"),
        "lane": observation_role_tag(task),
        "status": str(task.get("status") or "").strip().lower() or "completed",
        "status_label": display_status,
        "display_status": display_status,
        "observation_role": observation_role,
        "summary": str(summary).strip()[:120],
    }

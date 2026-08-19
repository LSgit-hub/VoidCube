"""Pure Supervisor room state projections that consume loaded snapshots."""

from __future__ import annotations

from typing import Any

from .ui_observation_projection import is_employee_lane_family_task
from .ui_projection import (
    observation_count,
    observation_group,
    observation_loop_stage,
)


def format_slot_overview(body_status: dict[str, Any]) -> str:
    active_slot = str(body_status.get("active_slot") or "").strip()
    shell_slot = str(body_status.get("shell_slot") or "").strip()
    if active_slot and shell_slot and active_slot != shell_slot:
        return f"{active_slot} / {shell_slot}"
    return active_slot or shell_slot or ""


def project_ui_metrics(
    chain_history_projection: list[dict[str, Any]],
    *,
    autonomous_observation: dict[str, Any],
    body_status: dict[str, Any],
    error_count: int,
) -> dict[str, Any]:
    counts = dict(autonomous_observation.get("counts") or {})
    body_improvement_projection_total = sum(
        1
        for task in chain_history_projection
        if str(task.get("execution_kind") or "").strip().lower() == "body_improvement"
    )
    learning_completed = sum(
        1
        for task in chain_history_projection
        if is_employee_lane_family_task(task) and task.get("status") == "completed"
    )
    learning_failed = sum(
        1
        for task in chain_history_projection
        if is_employee_lane_family_task(task) and task.get("status") == "failed"
    )
    followup_signal_count = 0
    judgement_record_count = 0
    priority_change_signal_count = 0
    for task in chain_history_projection:
        preview = dict(task.get("judgement_preview") or {})
        if isinstance(preview.get("followup_suggestion"), dict):
            followup_signal_count += 1
        if isinstance(preview.get("review_outcome"), dict):
            judgement_record_count += 1
        if isinstance(preview.get("priority_adjustment"), dict):
            priority_change_signal_count += 1

    return {
        "chain_projection": {
            "api_b_judgement": observation_count(counts.get("api_b_judgement")),
            "employee_running": observation_count(counts.get("employee_running")),
            "employee_dispatch": observation_count(counts.get("employee_dispatch")),
            "candidate_signals": observation_count(counts.get("candidates")),
            "writeback_history": observation_count(counts.get("writebacks")),
            "body_improvement": body_improvement_projection_total,
        },
        "learning_results": {
            "completed": learning_completed,
            "failed": learning_failed,
        },
        "slot_overview": format_slot_overview(body_status),
        "error_count": error_count,
        "observation": {
            "judgement_records": judgement_record_count,
            "followup_signals": followup_signal_count,
            "priority_change_signals": priority_change_signal_count,
        },
    }


def project_supervisor_scene(
    *,
    autonomous_observation: dict[str, Any],
    observation_input_available: bool,
    error_count: int = 0,
    memory_active: bool = False,
) -> tuple[str, str, str]:
    """Map an observation snapshot to a legal Supervisor scene."""
    error_note = f" · {error_count} recent error(s)" if error_count > 0 else ""
    board = dict(autonomous_observation.get("board") or {})
    board_focus = dict(board.get("primary_focus") or {})
    focus_title = str(board_focus.get("title") or "当前链路项").strip() or "当前链路项"
    focus_stage_key = str(board_focus.get("stage_key") or "").strip()
    focus_task_family = str(board_focus.get("task_family") or "").strip().lower()
    if not focus_task_family:
        focus_task = dict(board_focus.get("focus_task") or {})
        focus_task_family = str(focus_task.get("task_family") or "").strip().lower()

    judgement_group = observation_group(autonomous_observation, "api_b_judgement")
    judgement_count = observation_count(
        judgement_group.get("payload_count") or judgement_group.get("count")
    )
    judgement_focus = dict(judgement_group.get("focus_item") or {})
    judgement_focus_title = str(
        judgement_focus.get("title") or focus_title
    ).strip() or focus_title
    judgement_focus_family = str(
        judgement_focus.get("task_family") or focus_task_family
    ).strip().lower()

    candidate_group = observation_group(autonomous_observation, "api_b_candidates")
    candidate_count = observation_count(
        candidate_group.get("payload_count") or candidate_group.get("count")
    )
    candidate_focus = dict(candidate_group.get("focus_item") or {})

    api_b_stage = observation_loop_stage(autonomous_observation, "api_b_judgement")
    employee_stage = observation_loop_stage(autonomous_observation, "employee_execution")
    employee_focus = dict(employee_stage.get("focus_task") or {})
    employee_status = str(employee_stage.get("status") or "").strip().lower()

    if employee_status == "active" and employee_focus:
        return (
            "handoff",
            f"自主交接中{error_note}",
            f"「{employee_focus.get('title', '自主链路项')}」已交给 员工代理执行面处理，结果将写回 Mem 供下一轮监督者判断。",
        )

    api_b_stage_status = str(api_b_stage.get("status") or "").strip().lower()
    api_b_focus = dict(api_b_stage.get("focus_task") or {})
    api_b_focus_title = str(
        api_b_focus.get("title") or judgement_focus_title
    ).strip() or judgement_focus_title
    api_b_focus_family = str(
        api_b_focus.get("task_family") or judgement_focus_family
    ).strip().lower()
    if api_b_stage_status == "active" and api_b_focus:
        if "memory" in api_b_focus_family:
            return (
                "maintenance",
                f"正在整理记忆{error_note}",
                f"「{api_b_focus_title}」正在由 Supervisor 维护记忆连续性。",
            )
        return (
            "planning",
            f"正在安排判断事项{error_note}",
            f"「{api_b_focus_title}」正处在 API-B 判断过程中。",
        )

    if memory_active and not employee_focus:
        return (
            "memory",
            f"正在整理记忆{error_note}",
            "记忆模型正在执行压缩规则：衰减→桥接→升级→清退。",
        )

    if candidate_count and candidate_focus:
        metadata = dict(candidate_focus.get("metadata") or {})
        value_tags = ", ".join(
            metadata.get("core_values") or candidate_focus.get("value_tags") or []
        )
        utility_pct = int(
            (metadata.get("utility") or candidate_focus.get("utility") or 0) * 100
        )
        return (
            "drive",
            f"发现值得优先处理的事{error_note}",
            f"「{candidate_focus.get('title', '链路项')}」从核心价值中浮现 [{value_tags}]，价值度 {utility_pct}%，等待 API-B 判断。",
        )

    if judgement_count and focus_stage_key == "api_b_judgement":
        if "memory" in focus_task_family or "memory" in judgement_focus_family:
            title = judgement_focus_title or focus_title
            return (
                "maintenance",
                f"正在整理记忆{error_note}",
                f"API-B 正在整理「{title}」。",
            )
        return (
            "planning",
            f"正在安排判断事项{error_note}",
            f"API-B 正在判断 {judgement_count} 个链路项。",
        )

    if not observation_input_available:
        return "idle", "望着窗外", "网关暂不可用，房间先显示本地状态。"
    return "idle", f"在窗边休息{error_note}", "当前没有新的自主动作。"

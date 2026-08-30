"""Pure Supervisor room state projections that consume loaded snapshots."""

from __future__ import annotations

from typing import Any

from .ui_observation_projection import is_employee_lane_family_task
from .ui_projection import (
    observation_count,
    observation_group,
    observation_loop_stage,
)


SCENE_ROOM_LOCATIONS = {
    "idle": "sofa",
    "planning": "writing_desk",
    "drive": "writing_desk",
    "memory": "bookshelf",
    "maintenance": "bookshelf",
    "handoff": "computer_desk",
}

SCENE_ACTIONS = {
    "idle": "rest",
    "planning": "write",
    "drive": "write",
    "memory": "organize",
    "maintenance": "organize",
    "handoff": "work",
}


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


def _employee_result_disposition(task: dict[str, Any]) -> dict[str, Any]:
    metadata = dict(task.get("metadata") or {})
    return dict(metadata.get("employee_result_disposition") or {})


def _scene_projection(
    *,
    scene: str,
    title: str,
    summary: str,
    stage: str,
    task_id: str = "",
    mode: str = "",
) -> dict[str, str]:
    normalized_scene = str(scene or "idle").strip().lower() or "idle"
    return {
        "scene": normalized_scene,
        "room_location": SCENE_ROOM_LOCATIONS.get(normalized_scene, "sofa"),
        "action": SCENE_ACTIONS.get(normalized_scene, "rest"),
        "title": str(title or "").strip(),
        "summary": str(summary or "").strip(),
        "stage": str(stage or "idle").strip().lower() or "idle",
        "task_id": str(task_id or "").strip(),
        "mode": str(mode or "").strip().lower(),
    }


def project_supervisor_scene_state(
    *,
    autonomous_observation: dict[str, Any],
    observation_input_available: bool,
    error_count: int = 0,
    memory_active: bool = False,
    mode: str = "",
) -> dict[str, str]:
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
    employee_disposition = _employee_result_disposition(employee_focus)
    employee_result_status = str(
        employee_disposition.get("status") or ""
    ).strip().lower()
    employee_task_id = str(employee_focus.get("task_id") or "").strip()

    if employee_status in {"active", "ready", "stale"} and employee_focus:
        if mode == "daily_companion":
            return _scene_projection(
                scene="idle",
                title="日常陪伴中",
                summary={
                    "active": (
                        f"「{employee_focus.get('title', '自主链路项')}」正在由员工代理执行，"
                        "星子仍保持日常陪伴。"
                    ),
                    "stale": (
                        f"「{employee_focus.get('title', '自主链路项')}」在等待员工执行器恢复，"
                        "星子仍保持日常陪伴。"
                    ),
                }.get(
                    employee_status,
                    f"「{employee_focus.get('title', '自主链路项')}」已转交员工代理，"
                    "星子仍保持日常陪伴。",
                ),
                stage="running" if employee_status == "active" else "dispatched",
                task_id=employee_task_id,
                mode=mode,
            )
        if employee_status == "active":
            return _scene_projection(
                scene="handoff",
                title=f"正在派发员工任务{error_note}",
                summary=(
                    f"「{employee_focus.get('title', '自主链路项')}」正在由员工代理执行，"
                    "完成后先回传星子。"
                ),
                stage="running",
                task_id=employee_task_id,
                mode=mode,
            )
        if employee_status == "stale":
            return _scene_projection(
                scene="handoff",
                title=f"员工任务等待恢复{error_note}",
                summary=(
                    f"「{employee_focus.get('title', '自主链路项')}」已经派发，"
                    "当前等待员工执行器恢复。"
                ),
                stage="dispatched",
                task_id=employee_task_id,
                mode=mode,
            )
        return _scene_projection(
            scene="handoff",
            title=f"正在派发员工任务{error_note}",
            summary=(
                f"「{employee_focus.get('title', '自主链路项')}」已经派发，"
                "等待员工代理接手。"
            ),
            stage="dispatched",
            task_id=employee_task_id,
            mode=mode,
        )

    api_b_stage_status = str(api_b_stage.get("status") or "").strip().lower()
    api_b_focus = dict(api_b_stage.get("focus_task") or {})
    api_b_focus_title = str(
        api_b_focus.get("title") or judgement_focus_title
    ).strip() or judgement_focus_title
    api_b_focus_family = str(
        api_b_focus.get("task_family") or judgement_focus_family
    ).strip().lower()

    if employee_status == "returned" and employee_focus:
        if mode == "daily_companion":
            return _scene_projection(
                scene="idle",
                title={
                    "awaiting_user_report": "等待向用户回报",
                    "reported_to_user": "已向用户回报",
                }.get(employee_result_status, "日常陪伴中"),
                summary=(
                    f"「{employee_focus.get('title', '自主链路项')}」的执行结果已经回到星子，"
                    "当前仍保持日常陪伴状态。"
                ),
                stage=employee_result_status or "returned_to_xingzi",
                task_id=employee_task_id,
                mode=mode,
            )
        return _scene_projection(
            scene="planning",
            title={
                "returned_to_xingzi": "正在回收员工结果",
                "awaiting_user_report": "等待向用户回报",
                "reported_to_user": "已向用户回报",
                "awaiting_mem_review": "等待星子判断",
                "written_to_mem": "星子已处理 Mem",
                "mem_write_failed": "Mem 处理失败",
            }.get(employee_result_status, "正在回收员工结果"),
            summary=(
                f"「{employee_focus.get('title', '自主链路项')}」的执行结果已经回到星子，"
                f"当前阶段为 {employee_result_status or 'returned_to_xingzi'}。"
            ),
            stage=employee_result_status or "returned_to_xingzi",
            task_id=employee_task_id,
            mode=mode,
        )

    if candidate_count and candidate_focus and not judgement_count:
        metadata = dict(candidate_focus.get("metadata") or {})
        value_tags = ", ".join(
            metadata.get("core_values") or candidate_focus.get("value_tags") or []
        )
        utility_pct = int(
            (metadata.get("utility") or candidate_focus.get("utility") or 0) * 100
        )
        return _scene_projection(
            scene="planning",
            title=f"正在规划任务{error_note}",
            summary=(
                f"「{candidate_focus.get('title', '链路项')}」从内生驱动中形成候选"
                f" [{value_tags}]，价值度 {utility_pct}%，等待 API-B 判断。"
            ),
            stage="candidate",
            task_id=str(candidate_focus.get("task_id") or ""),
            mode=mode,
        )

    if api_b_stage_status == "active" and api_b_focus:
        if "memory" in api_b_focus_family:
            return _scene_projection(
                scene="maintenance",
                title=f"正在整理记忆{error_note}",
                summary=f"「{api_b_focus_title}」正在由 Supervisor 维护记忆连续性。",
                stage="memory_maintenance",
                task_id=str(api_b_focus.get("task_id") or ""),
                mode=mode,
            )
        return _scene_projection(
            scene="planning",
            title=f"正在规划任务{error_note}",
            summary=f"「{api_b_focus_title}」正处在 API-B 规划判断过程中。",
            stage="planning",
            task_id=str(api_b_focus.get("task_id") or ""),
            mode=mode,
        )

    if memory_active and not employee_focus:
        return _scene_projection(
            scene="memory",
            title=f"正在整理记忆{error_note}",
            summary="记忆模型正在执行压缩规则：衰减→桥接→升级→清退。",
            stage="memory_maintenance",
            mode=mode,
        )

    if judgement_count and focus_stage_key == "api_b_judgement":
        if "memory" in focus_task_family or "memory" in judgement_focus_family:
            title = judgement_focus_title or focus_title
            return _scene_projection(
                scene="maintenance",
                title=f"正在整理记忆{error_note}",
                summary=f"API-B 正在整理「{title}」。",
                stage="memory_maintenance",
                task_id=str(judgement_focus.get("task_id") or ""),
                mode=mode,
            )
        return _scene_projection(
            scene="planning",
            title=f"正在规划任务{error_note}",
            summary=f"API-B 正在规划判断 {judgement_count} 个链路项。",
            stage="planning",
            task_id=str(judgement_focus.get("task_id") or ""),
            mode=mode,
        )

    if not observation_input_available:
        return _scene_projection(
            scene="idle",
            title="望着窗外",
            summary="网关暂不可用，房间先显示本地状态。",
            stage="idle",
            mode=mode,
        )
    return _scene_projection(
        scene="idle",
        title=f"在窗边休息{error_note}",
        summary="当前没有新的自主动作。",
        stage="idle",
        mode=mode,
    )


def project_supervisor_scene(
    *,
    autonomous_observation: dict[str, Any],
    observation_input_available: bool,
    error_count: int = 0,
    memory_active: bool = False,
) -> tuple[str, str, str]:
    projected = project_supervisor_scene_state(
        autonomous_observation=autonomous_observation,
        observation_input_available=observation_input_available,
        error_count=error_count,
        memory_active=memory_active,
    )
    return projected["scene"], projected["title"], projected["summary"]

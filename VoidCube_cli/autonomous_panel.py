from __future__ import annotations

import time
from typing import Any, Dict

from VoidCube_cli.autonomous_observation import (
    resolve_autonomous_no_task_reason,
    resolve_autonomous_panel_focus_stage,
    resolve_autonomous_panel_focus_task,
    resolve_supervisor_stage_descriptor,
)


def build_autonomous_executor_lease_row(
    gateway_state: Dict[str, Any],
    inner_width: int,
    *,
    session_id: str,
    trim_status_bar_text: Any,
) -> tuple[str, str]:
    active = dict(gateway_state.get("active_cli_executor") or {})
    current_session_id = str(session_id or "").strip()
    active_session_id = str(active.get("session_id") or "").strip()
    if not active_session_id:
        text = "Executor: no live API-A autonomous executor registered yet"
        return "class:auto-panel-warn", trim_status_bar_text(text, inner_width)

    lease_status = str(active.get("lease_status") or "").strip().lower()
    idle_seconds = int(active.get("idle_seconds") or 0)
    scene = str(active.get("scene") or "idle").strip() or "idle"
    owner_label = (
        "this executor"
        if active_session_id == current_session_id
        else f"executor {active_session_id[-8:]}"
    )
    if lease_status == "stale" or bool(active.get("is_stale")):
        text = f"Executor: {owner_label} stale ({idle_seconds}s idle, scene={scene})"
        return "class:auto-panel-bad", trim_status_bar_text(text, inner_width)

    text = f"Executor: {owner_label} healthy ({idle_seconds}s idle, scene={scene})"
    if active_session_id != current_session_id:
        return "class:auto-panel-warn", trim_status_bar_text(text, inner_width)
    return "class:auto-panel-info", trim_status_bar_text(text, inner_width)


def resolve_autonomous_waiting_start_cause(
    events: list[Dict[str, Any]],
) -> tuple[str, str]:
    if not events:
        return (
            "class:auto-panel-warn",
            "近因: 已认领任务，但还没有收到后续执行事件",
        )
    latest = dict(events[-1] or {})
    stage = str(latest.get("stage") or "").strip().lower()
    if stage == "prompt_enqueue_failed":
        return (
            "class:auto-panel-bad",
            "近因: 执行提示注入前台 CLI 失败，任务未真正起跑",
        )
    if stage == "prompt_enqueued":
        return (
            "class:auto-panel-info",
            "近因: 执行提示已入队，正在等待首个模型响应",
        )
    if stage == "claim":
        return (
            "class:auto-panel-warn",
            "近因: 任务刚被认领，执行提示尚未注入前台 CLI",
        )
    if stage == "tool_started":
        return (
            "class:auto-panel-info",
            "近因: 已进入工具回合，等待工具返回结果",
        )
    if stage == "tool_completed":
        return (
            "class:auto-panel-info",
            "近因: 工具已返回，等待模型继续后续回合",
        )
    if stage == "model_turn_finished":
        return (
            "class:auto-panel-info",
            "近因: 模型回合已结束，等待任务写回阶段接管",
        )
    return (
        "class:auto-panel-dim",
        f"近因: {str(latest.get('message') or '暂无可用诊断').strip()}",
    )


def build_autonomous_execution_panel_rows(host: Any) -> list[tuple[str, str]]:
    width = host._get_tui_terminal_width()
    inner_width = max(34, min(width - 4, 92))
    session_short = str(getattr(host, "session_id", "") or "")[-8:] or "unknown"
    rows: list[tuple[str, str]] = []
    supervisor_state = host._fetch_supervisor_status()
    gateway_state = host._fetch_autonomous_gateway_status()
    focus_task = resolve_autonomous_panel_focus_task(
        supervisor_state,
        getattr(host, "_current_autonomous_task", None),
    )
    focus_stage = resolve_autonomous_panel_focus_stage(
        focus_task,
        current_task=getattr(host, "_current_autonomous_task", None),
        agent_running=bool(getattr(host, "_agent_running", False)),
        last_agent_turn_result=getattr(host, "_last_agent_turn_result", None),
    )
    supervisor_descriptor = resolve_supervisor_stage_descriptor(
        supervisor_state,
        focus_stage,
    )

    if focus_stage == "claimed_running":
        status_label = "执行中"
        status_style = "class:auto-panel-good"
    elif focus_stage == "claimed_waiting_writeback":
        status_label = "等待回写"
        status_style = "class:auto-panel-good"
    elif focus_stage == "claimed_waiting_start":
        status_label = "已认领待起跑"
        status_style = "class:auto-panel-warn"
    elif getattr(host, "_agent_running", False):
        status_label = "模型处理中"
        status_style = "class:auto-panel-good"
    elif focus_stage == "approved_waiting_claim":
        status_label = str(supervisor_descriptor.get("status_label") or "已放行待认领")
        status_style = "class:auto-panel-warn"
    elif focus_stage == "running_elsewhere":
        status_label = str(supervisor_descriptor.get("status_label") or "他处执行中")
        status_style = "class:auto-panel-info"
    else:
        status_label = str(supervisor_descriptor.get("status_label") or "待命拉单")
        status_style = "class:auto-panel-warn"

    rows.append(("class:auto-panel-title", f"Autonomous Executor · 会话 {session_short}"))
    rows.append((status_style, f"状态: {status_label}"))
    rows.append(
        build_autonomous_executor_lease_row(
            gateway_state,
            inner_width,
            session_id=str(getattr(host, "session_id", "") or ""),
            trim_status_bar_text=host._trim_status_bar_text,
        )
    )
    task_id = str(focus_task.get("task_id") or "").strip()
    task_title = str(focus_task.get("title") or "").strip()
    execution_kind = str(
        focus_task.get("execution_kind")
        or focus_task.get("task_family")
        or focus_task.get("task_type")
        or ""
    ).strip().lower()
    if task_id:
        label = "改进" if execution_kind == "body_improvement" else "学习"
        task_text = f"任务: {label} · {task_id[:8]} · {task_title or '(untitled)'}"
        current_task = getattr(host, "_current_autonomous_task", None)
        if focus_task is current_task:
            started_at = float(getattr(host, "_current_autonomous_task_started_at", 0.0) or 0.0)
            if started_at > 0:
                elapsed = max(0, int(time.time() - started_at))
                task_text += f" · {elapsed}s"
        rows.append(("class:auto-panel-text", host._trim_status_bar_text(task_text, inner_width)))
        if focus_stage == "claimed_waiting_start":
            rows.append(
                (
                    "class:auto-panel-warn",
                    host._trim_status_bar_text(
                        "链路: 自主执行面已认领该任务，等待进入首个模型或工具回合",
                        inner_width,
                    ),
                )
            )
            cause_style, cause_text = resolve_autonomous_waiting_start_cause(
                list(getattr(host, "_autonomous_execution_events", []) or [])
            )
            rows.append((cause_style, host._trim_status_bar_text(cause_text, inner_width)))
        elif focus_stage == "claimed_waiting_writeback":
            rows.append(
                (
                    "class:auto-panel-info",
                    host._trim_status_bar_text(
                        "链路: 自主执行面已完成执行，等待结果回写到任务链",
                        inner_width,
                    ),
                )
            )
        elif focus_stage == "approved_waiting_claim":
            rows.append(
                (
                    "class:auto-panel-warn",
                    host._trim_status_bar_text(
                        str(
                            supervisor_descriptor.get("chain_reason")
                            or "链路: 监督者已放行该任务，等待 API-A 自主执行面认领"
                        ),
                        inner_width,
                    ),
                )
            )
        elif focus_stage == "running_elsewhere":
            rows.append(
                (
                    "class:auto-panel-info",
                    host._trim_status_bar_text(
                        str(
                            supervisor_descriptor.get("chain_reason")
                            or "链路: 该任务已被其他 API-A 自主执行面认领"
                        ),
                        inner_width,
                    ),
                )
            )
    else:
        rows.append(("class:auto-panel-dim", "任务: 当前没有被认领的自主任务"))
        reason_style, reason_text = resolve_autonomous_no_task_reason(supervisor_state)
        rows.append((reason_style, host._trim_status_bar_text(reason_text, inner_width)))

    spinner_text = str(getattr(host, "_spinner_text", "") or "").strip()
    if spinner_text:
        activity_text = f"执行流: {spinner_text}"
    elif focus_stage == "claimed_running" or getattr(host, "_agent_running", False):
        activity_text = "执行流: 模型正在 API-A 自主执行面中工作"
    elif focus_stage == "claimed_waiting_start":
        activity_text = "执行流: API-A 自主执行面已认领任务，等待进入首个模型或工具回合"
    elif focus_stage == "claimed_waiting_writeback":
        activity_text = "执行流: API-A 自主执行面已结束本轮执行，等待写回任务状态"
    elif focus_stage == "approved_waiting_claim":
        activity_text = str(
            supervisor_descriptor.get("activity_text")
            or "执行流: 监督者已放行任务，等待 API-A 自主执行面认领"
        )
    elif focus_stage == "running_elsewhere":
        activity_text = str(
            supervisor_descriptor.get("activity_text")
            or "执行流: 任务正在其他 API-A 自主执行面中运行"
        )
    else:
        activity_text = str(
            supervisor_descriptor.get("activity_text")
            or "执行流: 等待监督者放行任务或等待下一轮拉单"
        )
    rows.append(("class:auto-panel-text", host._trim_status_bar_text(activity_text, inner_width)))

    timeline = list(supervisor_state.get("timeline") or [])
    if timeline:
        latest = dict(timeline[0] or {})
        latest_supervisor = str(latest.get("summary") or latest.get("title") or "").strip()
        if latest_supervisor:
            rows.append(
                (
                    "class:auto-panel-info",
                    host._trim_status_bar_text(f"监督: {latest_supervisor}", inner_width),
                )
            )

    for event in list(getattr(host, "_autonomous_execution_events", []) or [])[-3:]:
        tone = str(event.get("tone") or "info").strip().lower()
        style = {
            "success": "class:auto-panel-good",
            "warn": "class:auto-panel-warn",
            "error": "class:auto-panel-bad",
        }.get(tone, "class:auto-panel-dim")
        msg = f"{event.get('at', '--:--:--')} · {event.get('message', '')}"
        rows.append((style, host._trim_status_bar_text(msg, inner_width)))

    rows.append(("class:auto-panel-dim", "控制: /auto-q 退出"))
    return rows


def get_autonomous_execution_panel_fragments(host: Any):
    rows = build_autonomous_execution_panel_rows(host)
    if not rows:
        return []
    width = host._get_tui_terminal_width()
    inner_width = max(34, min(width - 4, 92))
    lines = []
    border_style = "class:auto-panel-border"

    lines.append((border_style, "╭" + ("─" * (inner_width + 2)) + "╮\n"))
    for style, text in rows:
        padded = host._pad_status_bar_text(text, inner_width)
        lines.append((border_style, "│ "))
        lines.append((style, padded))
        lines.append((border_style, " │\n"))
    lines.append((border_style, "╰" + ("─" * (inner_width + 2)) + "╯"))
    return lines

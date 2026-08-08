from __future__ import annotations

import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Dict

from VoidCube_cli.autonomous_observation import (
    observation_group_items,
    resolve_autonomous_no_task_reason,
    resolve_autonomous_panel_focus_stage,
    resolve_autonomous_panel_focus_task,
    resolve_supervisor_stage_descriptor,
)
from VoidCube_cli.autonomous_status_host import (
    fetch_autonomous_gateway_status,
    fetch_supervisor_status,
)


@dataclass(frozen=True, slots=True)
class AutonomousPanelRenderPorts:
    """Display metrics supplied by the terminal host."""

    terminal_width: Callable[[], int]
    trim_status_bar_text: Callable[[str, int], str]
    pad_status_bar_text: Callable[[str, int], str]


@dataclass(frozen=True, slots=True)
class AutonomousPanelStatePorts:
    """Read-only autonomous state supplied by the CLI host."""

    gate_active: Callable[[], bool]
    session_id: Callable[[], str]
    current_task: Callable[[], object | None]
    current_task_started_at: Callable[[], float]
    agent_running: Callable[[], bool]
    last_agent_turn_result: Callable[[], object | None]
    pending_input_nonempty: Callable[[], bool]
    execution_events: Callable[[], list[dict[str, object]]]
    spinner_text: Callable[[], str]
    scheduler_snapshot: Callable[[], object | None] | None = None
    scheduler_events: Callable[[], Sequence[Mapping[str, object]]] | None = None


def has_visible_autonomous_work(
    host: Any,
    *,
    state_ports: AutonomousPanelStatePorts,
) -> bool:
    """Return True when the autonomous execution panel should be visible."""
    if not state_ports.gate_active():
        return False
    if state_ports.agent_running():
        return True
    if state_ports.current_task():
        return True
    if state_ports.last_agent_turn_result():
        return True
    if state_ports.pending_input_nonempty():
        return True
    scheduler_snapshot = (
        state_ports.scheduler_snapshot() if state_ports.scheduler_snapshot else None
    )
    active = getattr(scheduler_snapshot, "active", None)
    queued = tuple(getattr(scheduler_snapshot, "queued", ()) or ())
    if getattr(getattr(active, "lane", None), "value", "") == "supervisor_task":
        return True
    if any(
        getattr(getattr(item, "lane", None), "value", "") == "supervisor_task"
        for item in queued
    ):
        return True
    visible_event_stages = {
        "claim",
        "autonomous_execution_started",
        "autonomous_execution_start_failed",
        "tool_started",
        "tool_completed",
        "model_turn_finished",
        "writeback",
        "writeback_failed",
        "improvement_report",
        "improvement_report_failed",
        "improvement_report_skipped",
    }
    for event in state_ports.execution_events()[-5:]:
        if str(event.get("stage") or "").strip().lower() in visible_event_stages:
            return True
    supervisor_state = fetch_supervisor_status(host)
    if _has_supervisor_chain_activity(supervisor_state):
        return True
    return False


def _has_supervisor_chain_activity(supervisor_state: Dict[str, Any]) -> bool:
    if not supervisor_state:
        return False
    if str(supervisor_state.get("scene") or "").strip().lower() not in {"", "idle"}:
        return True
    if dict(supervisor_state.get("lm_input") or {}):
        return True
    observation = dict(supervisor_state.get("autonomous_observation") or {})
    runtime = dict(observation.get("runtime") or {})
    if int(runtime.get("api_a_handoff_count") or 0) or int(runtime.get("api_a_running_count") or 0):
        return True
    for key in ("api_b_candidates", "api_b_judgement", "api_a_handoff"):
        if observation_group_items(supervisor_state, key):
            return True
    loop = dict(observation.get("loop") or {})
    for card in list(loop.get("stage_cards") or []):
        if not isinstance(card, dict):
            continue
        if str(card.get("status") or "").strip().lower() in {"active", "ready"}:
            return True
    return False


def _append_api_b_status_rows(
    rows: list[tuple[str, str]],
    supervisor_state: Dict[str, Any],
    inner_width: int,
    *,
    trim_status_bar_text: Any,
) -> None:
    lm_input = dict(supervisor_state.get("lm_input") or {})
    if lm_input:
        enabled = bool(lm_input.get("generation_enabled"))
        status = str(lm_input.get("status") or "").strip()
        role = str(lm_input.get("model_role") or "").strip()
        proposal_count = lm_input.get("proposal_count")
        details = ["LM生成=已启用" if enabled else "LM生成=未启用"]
        tier1_stats = dict(supervisor_state.get("tier1_stats") or {})
        if enabled:
            model = str(tier1_stats.get("llm_model") or "").strip()
            error = str(tier1_stats.get("llm_error") or "").strip()
            if "llm_healthy" in tier1_stats:
                model_suffix = f"({model})" if model else ""
                details.append(
                    f"模型=健康{model_suffix}"
                    if bool(tier1_stats.get("llm_healthy"))
                    else f"模型=异常{model_suffix}"
                )
            elif tier1_stats.get("memory_unavailable"):
                details.append("模型健康=未知")
            if error:
                details.append(f"原因={error}")
        if status:
            details.append(f"状态={status}")
        if role:
            details.append(f"角色={role}")
        if proposal_count is not None:
            details.append(f"提案={proposal_count}")
        rows.append(
            (
                "class:auto-panel-info" if enabled else "class:auto-panel-warn",
                trim_status_bar_text("API-B 模型: " + " · ".join(details), inner_width),
            )
        )

    candidate_items = observation_group_items(supervisor_state, "api_b_candidates")
    judgement_items = observation_group_items(supervisor_state, "api_b_judgement")
    if candidate_items or judgement_items:
        rows.append(
            (
                "class:auto-panel-info",
                trim_status_bar_text(
                    f"API-B 阶段: 候选={len(candidate_items)} · 判断在途={len(judgement_items)}",
                    inner_width,
                ),
            )
        )
        focus = dict((judgement_items or candidate_items)[0] or {})
        title = str(focus.get("title") or focus.get("summary") or "").strip()
        status = str(focus.get("display_status") or focus.get("status") or "").strip()
        if title:
            suffix = f" · {status}" if status else ""
            rows.append(
                (
                    "class:auto-panel-dim",
                    trim_status_bar_text(f"API-B 焦点: {title}{suffix}", inner_width),
                )
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
        text = "执行面: 当前还没有可见的 API-A 自主执行会话"
        return "class:auto-panel-warn", trim_status_bar_text(text, inner_width)

    lease_status = str(active.get("lease_status") or "").strip().lower()
    idle_seconds = int(active.get("idle_seconds") or 0)
    scene = str(active.get("scene") or "idle").strip() or "idle"
    owner_label = (
        "当前会话"
        if active_session_id == current_session_id
        else f"其他会话 {active_session_id[-8:]}"
    )
    if lease_status == "stale" or bool(active.get("is_stale")):
        text = f"执行面: {owner_label} 已陈旧（静默 {idle_seconds}s，场景 {scene}）"
        return "class:auto-panel-bad", trim_status_bar_text(text, inner_width)

    text = f"执行面: {owner_label} 正常（静默 {idle_seconds}s，场景 {scene}）"
    if active_session_id != current_session_id:
        return "class:auto-panel-warn", trim_status_bar_text(text, inner_width)
    return "class:auto-panel-info", trim_status_bar_text(text, inner_width)


def resolve_autonomous_waiting_start_cause(
    events: list[Dict[str, Any]],
) -> tuple[str, str]:
    if not events:
        return (
            "class:auto-panel-warn",
            "近因: 已认领链路项，但还没有收到后续执行事件",
        )
    latest = dict(events[-1] or {})
    stage = str(latest.get("stage") or "").strip().lower()
    if stage == "autonomous_execution_start_failed":
        return (
            "class:auto-panel-bad",
            "近因: 自主执行启动失败，链路项还没有真正进入首个执行回合",
        )
    if stage == "autonomous_execution_started":
        return (
            "class:auto-panel-info",
            "近因: 自主执行已起跑，正在等待首个模型响应",
        )
    if stage == "claim":
        return (
            "class:auto-panel-warn",
            "近因: 链路项刚被认领，尚未进入首个执行回合",
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
            "近因: 模型回合已结束，等待链路写回阶段接管",
        )
    return (
        "class:auto-panel-dim",
        f"近因: {str(latest.get('message') or '暂无可用诊断').strip()}",
    )


def build_autonomous_execution_panel_rows(
    host: Any,
    *,
    state_ports: AutonomousPanelStatePorts,
    render_ports: AutonomousPanelRenderPorts,
) -> list[tuple[str, str]]:
    width = render_ports.terminal_width()
    trim_status_bar_text = render_ports.trim_status_bar_text
    inner_width = max(1, min(width - 4, 92))
    session_short = state_ports.session_id()[-8:] or "unknown"
    rows: list[tuple[str, str]] = []
    supervisor_state = fetch_supervisor_status(host)
    gateway_state = fetch_autonomous_gateway_status(host)
    focus_task = resolve_autonomous_panel_focus_task(
        supervisor_state,
        state_ports.current_task(),
    )
    focus_stage = resolve_autonomous_panel_focus_stage(
        focus_task,
        current_task=state_ports.current_task(),
        agent_running=state_ports.agent_running(),
        last_agent_turn_result=state_ports.last_agent_turn_result(),
    )
    supervisor_descriptor = resolve_supervisor_stage_descriptor(
        supervisor_state,
        focus_stage,
    )
    scheduler_snapshot = (
        state_ports.scheduler_snapshot() if state_ports.scheduler_snapshot else None
    )

    if focus_stage == "local_claimed_active":
        status_label = "执行中"
        status_style = "class:auto-panel-good"
    elif focus_stage == "local_claimed_waiting_writeback":
        status_label = "等待回写"
        status_style = "class:auto-panel-good"
    elif focus_stage == "local_claimed_waiting_first_turn":
        status_label = "已认领待起跑"
        status_style = "class:auto-panel-warn"
    elif state_ports.agent_running():
        status_label = "模型处理中"
        status_style = "class:auto-panel-good"
    elif focus_stage == "waiting_api_a_claim":
        status_label = str(supervisor_descriptor.get("status_label") or "API-B 已转交")
        status_style = "class:auto-panel-warn"
    elif focus_stage == "running_on_other_api_a":
        status_label = str(supervisor_descriptor.get("status_label") or "他处执行中")
        status_style = "class:auto-panel-info"
    else:
        status_label = str(supervisor_descriptor.get("status_label") or "API-B 判断中")
        status_style = "class:auto-panel-warn"

    rows.append(("class:auto-panel-title", f"API-A 自主执行面 · 会话 {session_short}"))
    rows.append((status_style, f"状态: {status_label}"))
    _append_scheduler_rows(
        rows,
        scheduler_snapshot,
        inner_width,
        trim_status_bar_text=trim_status_bar_text,
        events=state_ports.scheduler_events() if state_ports.scheduler_events else (),
    )
    _append_api_b_status_rows(
        rows,
        supervisor_state,
        inner_width,
        trim_status_bar_text=trim_status_bar_text,
    )
    rows.append(
        build_autonomous_executor_lease_row(
            gateway_state,
            inner_width,
            session_id=state_ports.session_id(),
            trim_status_bar_text=trim_status_bar_text,
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
        task_text = f"链路项: {label} · {task_id[:8]} · {task_title or '未命名'}"
        current_task = state_ports.current_task()
        if focus_task is current_task:
            started_at = state_ports.current_task_started_at()
            if started_at > 0:
                elapsed = max(0, int(time.time() - started_at))
                task_text += f" · {elapsed}s"
        rows.append(("class:auto-panel-text", trim_status_bar_text(task_text, inner_width)))
        if focus_stage == "local_claimed_waiting_first_turn":
            rows.append(
                (
                    "class:auto-panel-warn",
                    trim_status_bar_text(
                        "链路: 自主执行面已认领该链路项，等待进入首个模型或工具回合",
                        inner_width,
                    ),
                )
            )
            cause_style, cause_text = resolve_autonomous_waiting_start_cause(
                state_ports.execution_events()
            )
            rows.append((cause_style, trim_status_bar_text(cause_text, inner_width)))
        elif focus_stage == "local_claimed_waiting_writeback":
            rows.append(
                (
                    "class:auto-panel-info",
                    trim_status_bar_text(
                        "链路: 自主执行面已完成执行，等待结果回写到自主链路",
                        inner_width,
                    ),
                )
            )
        elif focus_stage == "waiting_api_a_claim":
            rows.append(
                (
                    "class:auto-panel-warn",
                    trim_status_bar_text(
                        str(
                            supervisor_descriptor.get("chain_reason")
                            or "链路: API-B 已转交该链路项，可由 API-A 自主执行面接手"
                        ),
                        inner_width,
                    ),
                )
            )
        elif focus_stage == "running_on_other_api_a":
            rows.append(
                (
                    "class:auto-panel-info",
                    trim_status_bar_text(
                        str(
                            supervisor_descriptor.get("chain_reason")
                            or "链路: 该链路项已被其他 API-A 自主执行面认领"
                        ),
                        inner_width,
                    ),
                )
            )
    else:
        rows.append(("class:auto-panel-dim", "链路项: 当前没有被认领的自主链路项"))
        reason_style, reason_text = resolve_autonomous_no_task_reason(supervisor_state)
        rows.append((reason_style, trim_status_bar_text(reason_text, inner_width)))

    spinner_text = state_ports.spinner_text().strip()
    if spinner_text:
        activity_text = f"执行流: {spinner_text}"
    elif focus_stage == "local_claimed_active" or state_ports.agent_running():
        activity_text = "执行流: 模型正在 API-A 自主执行面中工作"
    elif focus_stage == "local_claimed_waiting_first_turn":
        activity_text = "执行流: API-A 自主执行面已认领链路项，等待进入首个模型或工具回合"
    elif focus_stage == "local_claimed_waiting_writeback":
        activity_text = "执行流: API-A 自主执行面已结束本轮执行，等待写回链路状态"
    elif focus_stage == "waiting_api_a_claim":
        activity_text = str(
            supervisor_descriptor.get("activity_text")
            or "执行流: API-A 认领后执行，结果写回 Mem"
        )
    elif focus_stage == "running_on_other_api_a":
        activity_text = str(
            supervisor_descriptor.get("activity_text")
            or "执行流: 链路项正在其他 API-A 自主执行面中运行"
        )
    else:
        activity_text = str(
            supervisor_descriptor.get("activity_text")
            or "执行流: API-B 判断、重排或再读取后再交给 API-A"
        )
    rows.append(("class:auto-panel-text", trim_status_bar_text(activity_text, inner_width)))

    timeline = list(supervisor_state.get("timeline") or [])
    if timeline:
        latest = dict(timeline[0] or {})
        latest_supervisor = str(latest.get("summary") or latest.get("title") or "").strip()
        if latest_supervisor:
            rows.append(
                (
                    "class:auto-panel-info",
                    trim_status_bar_text(f"监督: {latest_supervisor}", inner_width),
                )
            )

    for event in state_ports.execution_events()[-3:]:
        tone = str(event.get("tone") or "info").strip().lower()
        style = {
            "success": "class:auto-panel-good",
            "warn": "class:auto-panel-warn",
            "error": "class:auto-panel-bad",
        }.get(tone, "class:auto-panel-dim")
        msg = f"{event.get('at', '--:--:--')} · {event.get('message', '')}"
        rows.append((style, trim_status_bar_text(msg, inner_width)))

    rows.append(("class:auto-panel-dim", "控制: /auto-q 临时停用自主链路"))
    return rows


def _append_scheduler_rows(
    rows: list[tuple[str, str]],
    snapshot: object | None,
    inner_width: int,
    *,
    trim_status_bar_text: Any,
    events: Sequence[Mapping[str, object]] = (),
) -> None:
    """Render scheduler ownership and waiting state from the shared snapshot."""
    if snapshot is None:
        return
    active = getattr(snapshot, "active", None)
    queued = tuple(getattr(snapshot, "queued", ()) or ())
    gate = bool(getattr(snapshot, "autonomous_gate", False))
    blocked_reason = str(getattr(snapshot, "blocked_reason", "") or "")
    if active is not None:
        lane = getattr(getattr(active, "lane", None), "value", "")
        state = getattr(getattr(active, "state", None), "value", "")
        request_id = str(getattr(active, "request_id", "") or "")
        lane_label = "用户" if lane == "user_chat" else "自主"
        request_suffix = f" · #{request_id[-8:]}" if request_id else ""
        state_label = {
            "running": "执行中",
            "cancelling": "取消中",
        }.get(state, state or "活动")
        style = "class:auto-panel-warn" if state == "cancelling" else "class:auto-panel-info"
        rows.append(
            (
                style,
                trim_status_bar_text(
                    f"调度: {lane_label}{state_label}{request_suffix}",
                    inner_width,
                ),
            )
        )
    supervisor_queued = sum(
        1
        for item in queued
        if getattr(getattr(item, "lane", None), "value", "") == "supervisor_task"
    )
    if supervisor_queued:
        rows.append(
            (
                "class:auto-panel-warn",
                trim_status_bar_text(
                    f"调度: 自主队列等待 {supervisor_queued} 项",
                    inner_width,
                ),
            )
        )
    if not gate and blocked_reason:
        rows.append(
            (
                "class:auto-panel-warn",
                trim_status_bar_text(f"调度: 自主门控关闭 · {blocked_reason}", inner_width),
            )
        )
    latest = dict(events[-1] or {}) if events else {}
    event_kind = str(latest.get("kind") or "").strip()
    if event_kind in {"waiting", "cancel_requested", "failed", "cancelled"}:
        request_id = str(latest.get("request_id") or "")
        request_suffix = f" · #{request_id[-8:]}" if request_id else ""
        reason = str(
            latest.get("reason") or latest.get("blocked_reason") or event_kind
        ).strip()
        rows.append(
            (
                "class:auto-panel-warn" if event_kind != "failed" else "class:auto-panel-bad",
                trim_status_bar_text(
                    f"调度事件: {event_kind}{request_suffix} · {reason}",
                    inner_width,
                ),
            )
        )


def get_autonomous_execution_panel_fragments(
    host: Any,
    *,
    state_ports: AutonomousPanelStatePorts,
    render_ports: AutonomousPanelRenderPorts,
):
    rows = build_autonomous_execution_panel_rows(
        host,
        state_ports=state_ports,
        render_ports=render_ports,
    )
    if not rows:
        return []
    width = render_ports.terminal_width()
    inner_width = max(1, min(width - 4, 92))
    lines = []
    border_style = "class:auto-panel-border"

    lines.append((border_style, "╭" + ("─" * (inner_width + 2)) + "╮\n"))
    for style, text in rows:
        padded = render_ports.pad_status_bar_text(text, inner_width)
        lines.append((border_style, "│ "))
        lines.append((style, padded))
        lines.append((border_style, " │\n"))
    lines.append((border_style, "╰" + ("─" * (inner_width + 2)) + "╯"))
    return lines

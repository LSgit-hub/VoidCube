"""Unified mini-CLI projection for the autonomous chain."""

from __future__ import annotations

import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any, Dict

from .observation import (
    observation_group_items,
    resolve_autonomous_panel_focus_stage,
    resolve_autonomous_panel_focus_task,
    resolve_supervisor_stage_descriptor,
)
from .status_host import (
    fetch_supervisor_status,
)

# ═══════════════════════════════════════════════════════════════════════
# Ports
# ═══════════════════════════════════════════════════════════════════════


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
    companion_tasks: Callable[[], Sequence[object]] | None = None


# ═══════════════════════════════════════════════════════════════════════
# Visibility gate
# ═══════════════════════════════════════════════════════════════════════


def has_visible_autonomous_work(
    host: Any,
    *,
    state_ports: AutonomousPanelStatePorts,
) -> bool:
    """Return True when the autonomous execution panel should be visible."""
    companion_tasks = tuple(
        state_ports.companion_tasks() if state_ports.companion_tasks else ()
    )
    if companion_tasks:
        return True
    if state_ports.agent_running():
        return True
    if state_ports.current_task():
        return True
    if state_ports.last_agent_turn_result():
        return True
    if state_ports.pending_input_nonempty():
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
    now = time.monotonic()
    for event in state_ports.execution_events()[-5:]:
        visible_until = event.get("visible_until")
        if visible_until is not None:
            try:
                if float(visible_until) > now:
                    return True
            except (TypeError, ValueError):
                pass
    if not state_ports.gate_active():
        return False
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
    if int(runtime.get("employee_dispatch_count") or 0) or int(runtime.get("employee_running_count") or 0):
        return True
    for key in ("api_b_candidates", "api_b_judgement", "employee_dispatch"):
        if observation_group_items(supervisor_state, key):
            return True
    loop = dict(observation.get("loop") or {})
    for card in list(loop.get("stage_cards") or []):
        if not isinstance(card, dict):
            continue
        if str(card.get("status") or "").strip().lower() in {"active", "ready"}:
            return True
    return False


# ═══════════════════════════════════════════════════════════════════════
# Row builders (each returns list of (style, text))
# ═══════════════════════════════════════════════════════════════════════

def _section_divider(label: str, inner_width: int, trim: Any) -> list[tuple[str, str]]:
    """Draw a thin labelled divider: ── label ──────────────"""
    remaining = inner_width - len(label) - 4
    if remaining < 2:
        return [("class:mc-separator", "─" * inner_width)]
    left = remaining // 2
    right = remaining - left
    return [("class:mc-separator", f"{'─' * left} {label} {'─' * right}")]


def _build_header_rows(
    session_id: str,
    status_label: str,
    focus_stage: str,
    inner_width: int,
    trim: Any,
) -> list[tuple[str, str]]:
    """Build the filled header bar with session tag and status."""
    session_short = session_id[-8:] or "????"
    rows: list[tuple[str, str]] = []

    # One display identity is shared by Auto and daily companion execution.
    rows.append((
        "class:mc-header-bg",
        trim(
            f" ◆ 自主链路迷你 CLI   会话 {session_short}",
            inner_width,
        ),
    ))
    # Status line below header
    status_icon = {
        "local_claimed_active": "●",
        "local_claimed_waiting_writeback": "◉",
        "local_claimed_waiting_first_turn": "◇",
        "waiting_employee_claim": "○",
        "running_on_other_employee": "◌",
    }.get(focus_stage, "○")
    rows.append((
        "class:mc-body-text",
        f"  {status_icon} {status_label}",
    ))
    return rows


def _build_task_rows(
    focus_task: dict | None,
    focus_stage: str,
    current_task: object | None,
    current_task_started_at: float,
    supervisor_descriptor: dict,
    execution_events: list[dict[str, object]],
    inner_width: int,
    trim: Any,
) -> list[tuple[str, str]]:
    """Show the current autonomous chain task."""
    rows: list[tuple[str, str]] = []

    if not focus_task:
        rows.append(("class:mc-body-dim", "  暂无被认领的链路项"))
        return rows

    task_id = str(focus_task.get("task_id") or "").strip()
    task_title = str(focus_task.get("title") or "").strip()
    execution_kind = str(
        focus_task.get("execution_kind")
        or focus_task.get("task_family")
        or focus_task.get("task_type")
        or ""
    ).strip().lower()
    kind_label = "改进" if execution_kind == "body_improvement" else "任务"

    task_text = f"  {kind_label} · {task_id[:8]} · {task_title or '未命名'}"
    if focus_task is current_task and current_task_started_at > 0:
        elapsed = max(0, int(time.time() - current_task_started_at))
        if elapsed < 60:
            task_text += f" · {elapsed}s"
        else:
            task_text += f" · {elapsed // 60}m{elapsed % 60}s"

    rows.append(("class:mc-body-text", trim(task_text, inner_width)))

    # Stage-specific detail
    if focus_stage == "local_claimed_waiting_first_turn":
        rows.append((
            "class:mc-status-warn",
            trim("     ⏳ 已认领，等待进入首个回合...", inner_width),
        ))
        cause_style, cause_text = _resolve_waiting_cause(execution_events)
        rows.append((cause_style, trim(f"     {cause_text}", inner_width)))
    elif focus_stage == "local_claimed_waiting_writeback":
        rows.append((
            "class:mc-status-active",
            "     ↩ 执行完成，等待结果写回",
        ))
    elif focus_stage == "waiting_employee_claim":
        rows.append((
            "class:mc-status-warn",
            trim(
                f"     {supervisor_descriptor.get('chain_reason', 'API-B 已转交，等待认领')}",
                inner_width,
            ),
        ))
    elif focus_stage == "running_on_other_employee":
        rows.append((
            "class:mc-body-accent",
            trim(
                f"     {supervisor_descriptor.get('chain_reason', '其他执行面处理中')}",
                inner_width,
            ),
        ))

    return rows


def _resolve_waiting_cause(events: list[dict[str, object]]) -> tuple[str, str]:
    """Return (style, text) for why a claimed task hasn't started yet."""
    if not events:
        return ("class:mc-status-warn", "已认领但未收到后续事件")
    latest = dict(events[-1] or {})
    stage = str(latest.get("stage") or "").strip().lower()
    if stage == "autonomous_execution_start_failed":
        return ("class:mc-status-error", "启动失败，首个回合未执行")
    if stage == "autonomous_execution_started":
        return ("class:mc-status-active", "已起跑，等待首个模型响应")
    if stage == "claim":
        return ("class:mc-status-warn", "刚被认领，尚未进入执行")
    if stage == "tool_started":
        return ("class:mc-status-active", "工具执行中，等待返回")
    if stage == "tool_completed":
        return ("class:mc-status-active", "工具已返回，等待模型继续")
    return ("class:mc-body-dim", str(latest.get("message", "等待中")).strip())


def _build_flow_rows(
    focus_stage: str,
    agent_running: bool,
    spinner_text: str,
    supervisor_descriptor: dict,
    inner_width: int,
    trim: Any,
) -> list[tuple[str, str]]:
    """Show execution flow / activity description."""
    rows: list[tuple[str, str]] = []

    if spinner_text.strip():
        activity = f"  {spinner_text}"
        rows.append(("class:mc-status-active", trim(activity, inner_width)))
    elif focus_stage == "local_claimed_active" or agent_running:
        rows.append(("class:mc-status-active", "  员工代理执行面工作中"))
    elif focus_stage == "local_claimed_waiting_first_turn":
        rows.append(("class:mc-body-accent", "  已认领链路项，等待首个回合"))
    elif focus_stage == "local_claimed_waiting_writeback":
        rows.append(("class:mc-body-accent", "  本轮结束，写回链路状态中"))
    elif focus_stage == "waiting_employee_claim":
        desc = supervisor_descriptor.get("activity_text", "员工代理认领后执行，结果写回 Mem")
        rows.append(("class:mc-body-text", f"  {desc}"))
    elif focus_stage == "running_on_other_employee":
        desc = supervisor_descriptor.get("activity_text", "链路项在其他执行面运行中")
        rows.append(("class:mc-body-accent", f"  {desc}"))
    else:
        desc = supervisor_descriptor.get("activity_text", "API-B 判断、重排或读取中")
        rows.append(("class:mc-body-dim", f"  {desc}"))

    return rows


def _build_timeline_rows(
    execution_events: list[dict[str, object]],
    supervisor_state: dict,
    inner_width: int,
    trim: Any,
) -> list[tuple[str, str]]:
    """Show recent execution events and supervisor timeline."""
    rows: list[tuple[str, str]] = []

    # Supervisor latest summary
    timeline = list(supervisor_state.get("timeline") or [])
    if timeline:
        latest = dict(timeline[0] or {})
        summary = str(latest.get("summary") or latest.get("title") or "").strip()
        if summary:
            rows.append(("class:mc-body-accent", trim(f"  {summary}", inner_width)))

    # Recent execution events (last 3)
    for event in execution_events[-3:]:
        tone = str(event.get("tone") or "info").strip().lower()
        style = {
            "success": "class:mc-status-success",
            "warn": "class:mc-status-warn",
            "error": "class:mc-status-error",
        }.get(tone, "class:mc-body-dim")
        msg = f"  {event.get('at', '--:--:--')}  {event.get('message', '')}"
        rows.append((style, trim(msg, inner_width)))

    return rows


# ═══════════════════════════════════════════════════════════════════════
# Main row builder
# ═══════════════════════════════════════════════════════════════════════


def build_autonomous_execution_panel_rows(
    host: Any,
    *,
    state_ports: AutonomousPanelStatePorts,
    render_ports: AutonomousPanelRenderPorts,
) -> list[tuple[str, str]]:
    """Build the full panel as a flat list of (style_class, text) rows."""
    width = render_ports.terminal_width()
    trim = render_ports.trim_status_bar_text
    inner_width = max(1, min(width - 4, 96))
    session_id = state_ports.session_id()

    # ── Fetch external state ──────────────────────────────────────────
    supervisor_state = fetch_supervisor_status(host)
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
    companion_tasks = tuple(
        state_ports.companion_tasks() if state_ports.companion_tasks else ()
    )
    recent_companion_event = None
    if not state_ports.gate_active():
        recent_companion_event = next(
            (
                event
                for event in reversed(state_ports.execution_events())
                if str(event.get("stage") or "").startswith("companion_")
            ),
            None,
        )
    companion_context = bool(companion_tasks or recent_companion_event)

    if companion_context:
        mode_label = "辅助模式"
        if companion_tasks:
            status_label = "员工执行中"
            focus_stage = "companion_active"
        elif str((recent_companion_event or {}).get("tone") or "") == "error":
            status_label = "最近任务失败"
            focus_stage = "companion_failed"
        else:
            status_label = "最近任务完成"
            focus_stage = "companion_completed"
    else:
        mode_label = "AUTO 模式" if state_ports.gate_active() else "辅助模式"

    # ── Resolve AUTO status label ─────────────────────────────────────
    if not companion_context and focus_stage == "local_claimed_active":
        status_label = "执行中"
    elif not companion_context and focus_stage == "local_claimed_waiting_writeback":
        status_label = "等待回写"
    elif not companion_context and focus_stage == "local_claimed_waiting_first_turn":
        status_label = "已认领 · 待起跑"
    elif not companion_context and state_ports.agent_running():
        status_label = "模型处理中"
    elif not companion_context and focus_stage == "waiting_employee_claim":
        status_label = str(supervisor_descriptor.get("status_label") or "API-B 已转交")
    elif not companion_context and focus_stage == "running_on_other_employee":
        status_label = str(supervisor_descriptor.get("status_label") or "他处执行中")
    elif not companion_context:
        status_label = str(supervisor_descriptor.get("status_label") or "API-B 判断中")

    # ── Assemble rows ─────────────────────────────────────────────────
    rows: list[tuple[str, str]] = []

    # 1. Header bar
    rows.extend(_build_header_rows(session_id, status_label, focus_stage, inner_width, trim))
    rows.append(("class:mc-body-accent", f"  模式 · {mode_label}"))

    if companion_context:
        rows.append(("", ""))
        rows.extend(_section_divider("执行任务", inner_width, trim))
        if companion_tasks:
            for task in companion_tasks[:4]:
                label = str(getattr(task, "prompt_preview", "") or "自主任务").strip()
                started_at = float(getattr(task, "started_at", 0.0) or 0.0)
                elapsed = max(0, int(time.time() - started_at)) if started_at else 0
                rows.append((
                    "class:mc-status-active",
                    trim(f"  ● {label} · {elapsed}s", inner_width),
                ))
            if len(companion_tasks) > 4:
                rows.append(("class:mc-body-dim", f"  另有 {len(companion_tasks) - 4} 项执行中"))
        else:
            rows.append(("class:mc-body-dim", "  当前没有员工任务在执行"))
        timeline_rows = _build_timeline_rows(
            state_ports.execution_events(), {}, inner_width, trim,
        )
        if timeline_rows:
            rows.append(("", ""))
            rows.extend(_section_divider("最近状态", inner_width, trim))
            rows.extend(timeline_rows)
        return rows

    # Auto mode keeps the same compact task/event projection.
    task_rows = _build_task_rows(
        focus_task, focus_stage,
        state_ports.current_task(), state_ports.current_task_started_at(),
        supervisor_descriptor,
        state_ports.execution_events(),
        inner_width, trim,
    )
    if task_rows:
        rows.append(("", ""))  # spacer
        rows.extend(_section_divider("链路项", inner_width, trim))
        rows.extend(task_rows)

    # Execution status
    flow_rows = _build_flow_rows(
        focus_stage, state_ports.agent_running(), state_ports.spinner_text(),
        supervisor_descriptor, inner_width, trim,
    )
    if flow_rows:
        rows.append(("", ""))  # spacer
        rows.extend(_section_divider("执行流", inner_width, trim))
        rows.extend(flow_rows)

    # Recent lifecycle events
    timeline_rows = _build_timeline_rows(
        state_ports.execution_events(), supervisor_state, inner_width, trim,
    )
    if timeline_rows:
        rows.append(("", ""))  # spacer
        rows.extend(_section_divider("最近事件", inner_width, trim))
        rows.extend(timeline_rows)

    return rows


# ═══════════════════════════════════════════════════════════════════════
# Fragment assembler (border + rows → prompt_toolkit fragments)
# ═══════════════════════════════════════════════════════════════════════


def get_autonomous_execution_panel_fragments(
    host: Any,
    *,
    state_ports: AutonomousPanelStatePorts,
    render_ports: AutonomousPanelRenderPorts,
) -> list[tuple[str, str]]:
    """Return styled FormattedText fragments for the TUI panel."""
    rows = build_autonomous_execution_panel_rows(
        host,
        state_ports=state_ports,
        render_ports=render_ports,
    )
    if not rows:
        return []

    width = render_ports.terminal_width()
    inner_width = max(1, min(width - 4, 96))
    pad = render_ports.pad_status_bar_text

    lines: list[tuple[str, str]] = []

    # Top border with label
    lines.append(("class:mc-border", "╭" + "─" * (inner_width + 2) + "╮\n"))

    for style, text in rows:
        if text == "":
            # blank spacer row
            lines.append(("class:mc-border", "│"))
            lines.append(("class:mc-panel-bg", " " * (inner_width + 2)))
            lines.append(("class:mc-border", "│\n"))
        else:
            padded = pad(text, inner_width)
            lines.append(("class:mc-border", "│ "))
            lines.append((style, padded))
            lines.append(("class:mc-border", " │\n"))

    # Bottom border
    lines.append(("class:mc-border", "╰" + "─" * (inner_width + 2) + "╯"))

    return lines

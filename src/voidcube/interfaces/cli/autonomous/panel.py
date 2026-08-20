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
# Compact field resolvers (代理 / 目标 / 状态)
# ═══════════════════════════════════════════════════════════════════════

_MAX_INNER_WIDTH = 64

_STATUS_DOTS: dict[str, tuple[str, str]] = {
    "local_claimed_active": ("●", "class:mc-status-active"),
    "local_claimed_waiting_writeback": ("◐", "class:mc-status-warn"),
    "local_claimed_waiting_first_turn": ("◌", "class:mc-status-warn"),
    "waiting_employee_claim": ("○", "class:mc-status-warn"),
    "running_on_other_employee": ("◆", "class:mc-status-info"),
    "companion_active": ("●", "class:mc-status-active"),
    "companion_completed": ("✓", "class:mc-status-success"),
    "companion_failed": ("✖", "class:mc-status-error"),
}

_IDLE_DOT: tuple[str, str] = ("○", "class:mc-status-idle")


def _resolve_agent(
    focus_stage: str,
    session_id: str,
    companion_context: bool,
    companion_count: int,
) -> str:
    """Resolve which agent is carrying the current task."""
    if companion_context:
        return f"员工代理 ×{companion_count}" if companion_count > 1 else "员工代理"
    session_tail = (session_id or "")[-8:]
    if focus_stage in {
        "local_claimed_active",
        "local_claimed_waiting_first_turn",
        "local_claimed_waiting_writeback",
    }:
        return f"员工代理 · {session_tail}" if session_tail else "员工代理"
    if focus_stage == "running_on_other_employee":
        return "员工代理（他处执行）"
    if focus_stage == "waiting_employee_claim":
        return "员工代理（待认领）"
    return "API-B"


def _resolve_goal(
    focus_task: dict | None,
    companion_tasks: Sequence[object],
    companion_context: bool,
) -> str:
    """Resolve the current task goal as a single readable line."""
    if companion_context:
        if companion_tasks:
            first = companion_tasks[0]
            return (
                str(getattr(first, "prompt_preview", "") or "自主任务").strip()
                or "自主任务"
            )
        return "自主任务"
    if focus_task:
        title = str(focus_task.get("title") or "").strip()
        if title:
            return title
        summary = str(focus_task.get("summary") or "").strip()
        if summary:
            return summary
    return "暂无任务"


# ═══════════════════════════════════════════════════════════════════════
# Main row builder
# ═══════════════════════════════════════════════════════════════════════


def build_autonomous_execution_panel_rows(
    host: Any,
    *,
    state_ports: AutonomousPanelStatePorts,
    render_ports: AutonomousPanelRenderPorts,
) -> list[tuple[str, str]]:
    """Build a minimal panel: 代理 / 目标 / 状态 only."""
    width = render_ports.terminal_width()
    trim = render_ports.trim_status_bar_text
    inner_width = max(1, min(width - 4, _MAX_INNER_WIDTH))
    session_id = state_ports.session_id()

    # ── Resolve external state ────────────────────────────────────────
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
        if companion_tasks:
            focus_stage = "companion_active"
        elif str((recent_companion_event or {}).get("tone") or "") == "error":
            focus_stage = "companion_failed"
        else:
            focus_stage = "companion_completed"

    # ── Resolve the three fields: 代理 / 目标 / 状态 ────────────────────
    agent_label = _resolve_agent(
        focus_stage, session_id, companion_context, len(companion_tasks),
    )
    goal_label = _resolve_goal(focus_task, companion_tasks, companion_context)

    if companion_context:
        status_label = {
            "companion_active": "执行中",
            "companion_completed": "已完成",
            "companion_failed": "失败",
        }[focus_stage]
    elif focus_stage == "local_claimed_active":
        status_label = "执行中"
    elif focus_stage == "local_claimed_waiting_writeback":
        status_label = "等待回写"
    elif focus_stage == "local_claimed_waiting_first_turn":
        status_label = "待起跑"
    elif state_ports.agent_running():
        status_label = "模型处理中"
    else:
        status_label = str(
            supervisor_descriptor.get("status_label") or "API-B 判断中"
        )

    dot, dot_style = _STATUS_DOTS.get(focus_stage, _IDLE_DOT)

    # ── Assemble three labelled rows ──────────────────────────────────
    return [
        ("class:mc-body-text", trim(f"  代理  {agent_label}", inner_width)),
        ("class:mc-body-text", trim(f"  目标  {goal_label}", inner_width)),
        (dot_style, trim(f"  状态  {dot} {status_label}", inner_width)),
    ]


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
    inner_width = max(1, min(width - 4, _MAX_INNER_WIDTH))
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

"""Autonomous execution panel — mini‑CLI display surface.

Renders a compact, visually distinct panel inside the main TUI so the
API‑A autonomous lane and API‑B worker tasks are easy to tell apart from
the user's own conversation.
"""

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
    scheduler_snapshot: Callable[[], object | None] | None = None
    scheduler_events: Callable[[], Sequence[Mapping[str, object]]] | None = None


# ═══════════════════════════════════════════════════════════════════════
# Visibility gate
# ═══════════════════════════════════════════════════════════════════════


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
    status_style: str,
    focus_stage: str,
    inner_width: int,
    trim: Any,
) -> list[tuple[str, str]]:
    """Build the filled header bar with session tag and status."""
    session_short = session_id[-8:] or "????"
    rows: list[tuple[str, str]] = []

    # Header bar: [◆ API-A 迷你CLI]  会话 a1b2c3d4  ·  ● 执行中
    rows.append((
        "class:mc-header-bg",
        trim(
            f" ◆ API-A 迷你CLI   会话 {session_short}",
            inner_width,
        ),
    ))
    # Status line below header
    status_icon = {
        "local_claimed_active": "●",
        "local_claimed_waiting_writeback": "◉",
        "local_claimed_waiting_first_turn": "◇",
        "waiting_api_a_claim": "○",
        "running_on_other_api_a": "◌",
    }.get(focus_stage, "○")
    rows.append((
        "class:mc-body-text",
        f"  {status_icon} {status_label}",
    ))
    return rows


def _build_api_b_status_rows(
    supervisor_state: Dict[str, Any],
    inner_width: int,
    trim: Any,
) -> list[tuple[str, str]]:
    """Show API-B model health and judgement state."""
    rows: list[tuple[str, str]] = []
    lm_input = dict(supervisor_state.get("lm_input") or {})

    if not lm_input and not observation_group_items(supervisor_state, "api_b_candidates"):
        return rows

    parts: list[tuple[str, str]] = []  # (style, text-part)

    if lm_input:
        enabled = bool(lm_input.get("generation_enabled"))
        tier1 = dict(supervisor_state.get("tier1_stats") or {})
        model = str(tier1.get("llm_model") or "").strip()
        healthy = tier1.get("llm_healthy")
        error = str(tier1.get("llm_error") or "").strip()

        if enabled:
            if model:
                short_model = model.rsplit("/", 1)[-1][:24]
                if healthy is True:
                    parts.append(("class:mc-status-success", f"模型: {short_model} ✓"))
                elif healthy is False:
                    reason = f" ({error})" if error else ""
                    parts.append(("class:mc-status-error", f"模型异常{reason}"))
                else:
                    parts.append(("class:mc-status-warn", f"模型: {short_model}"))
            else:
                parts.append(("class:mc-body-dim", "LM生成: 已启用"))
        else:
            parts.append(("class:mc-body-dim", "LM生成: 关闭"))

        status = str(lm_input.get("status") or "").strip()
        if status:
            parts.append(("class:mc-body-dim", f"状态: {status}"))

    candidate_items = observation_group_items(supervisor_state, "api_b_candidates")
    judgement_items = observation_group_items(supervisor_state, "api_b_judgement")

    if candidate_items or judgement_items:
        parts.append((
            "class:mc-body-accent",
            f"候选 {len(candidate_items)} · 判断 {len(judgement_items)}",
        ))

    if parts:
        joined = _join_fragments(parts, " · ")
        rows.append(("class:mc-body-text", f"  API-B {joined}"))

    # Focus item from candidates/judgements
    focus = None
    if judgement_items:
        focus = dict(judgement_items[0] or {})
    elif candidate_items:
        focus = dict(candidate_items[0] or {})
    if focus:
        title = str(focus.get("title") or focus.get("summary") or "").strip()
        status = str(focus.get("display_status") or focus.get("status") or "").strip()
        if title:
            suffix = f" · {status}" if status else ""
            rows.append((
                "class:mc-body-dim",
                trim(f"     ↳ {title}{suffix}", inner_width),
            ))

    return rows


def _build_scheduler_rows(
    snapshot: object | None,
    inner_width: int,
    trim: Any,
    events: Sequence[Mapping[str, object]] = (),
) -> list[tuple[str, str]]:
    """Show scheduler ownership and queue state."""
    rows: list[tuple[str, str]] = []
    if snapshot is None:
        return rows

    active = getattr(snapshot, "active", None)
    queued = tuple(getattr(snapshot, "queued", ()) or ())
    gate = bool(getattr(snapshot, "autonomous_gate", False))
    blocked_reason = str(getattr(snapshot, "blocked_reason", "") or "")

    if active is not None:
        lane = getattr(getattr(active, "lane", None), "value", "")
        state = getattr(getattr(active, "state", None), "value", "")
        request_id = str(getattr(active, "request_id", "") or "")
        lane_icon = "◆" if lane == "supervisor_task" else "●"
        lane_label = "自主" if lane == "supervisor_task" else "用户"
        request_suffix = f" #{request_id[-8:]}" if request_id else ""
        state_label = {
            "running": "执行中",
            "cancelling": "取消中",
        }.get(state, state or "活动")
        style = "class:mc-status-warn" if state == "cancelling" else "class:mc-status-active"
        rows.append((
            style,
            trim(f"  {lane_icon} {lane_label}{state_label}{request_suffix}", inner_width),
        ))

    supervisor_queued = sum(
        1 for item in queued
        if getattr(getattr(item, "lane", None), "value", "") == "supervisor_task"
    )
    if supervisor_queued:
        rows.append((
            "class:mc-status-warn",
            f"  ◇ 队列 {supervisor_queued} 项等待",
        ))

    if not gate and blocked_reason:
        rows.append((
            "class:mc-status-warn",
            trim(f"  门控关闭 · {blocked_reason}", inner_width),
        ))

    # Latest scheduler event if interesting
    latest = dict(events[-1] or {}) if events else {}
    event_kind = str(latest.get("kind") or "").strip()
    if event_kind in {"waiting", "cancel_requested", "failed", "cancelled"}:
        request_id = str(latest.get("request_id") or "")
        request_suffix = f" #{request_id[-8:]}" if request_id else ""
        reason = str(latest.get("reason") or latest.get("blocked_reason") or event_kind).strip()
        style = "class:mc-status-error" if event_kind == "failed" else "class:mc-status-warn"
        rows.append((
            style,
            trim(f"  ! {event_kind}{request_suffix} · {reason}", inner_width),
        ))

    return rows


def _build_task_rows(
    focus_task: dict | None,
    focus_stage: str,
    current_task: object | None,
    current_task_started_at: float,
    supervisor_descriptor: dict,
    supervisor_state: dict,
    execution_events: list[dict[str, object]],
    inner_width: int,
    trim: Any,
) -> list[tuple[str, str]]:
    """Show the current autonomous chain task."""
    rows: list[tuple[str, str]] = []

    if not focus_task:
        rows.append(("class:mc-body-dim", "  暂无被认领的链路项"))
        reason_style, reason_text = resolve_autonomous_no_task_reason(supervisor_state)
        if reason_text:
            rows.append((reason_style, trim(f"  {reason_text}", inner_width)))
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
    elif focus_stage == "waiting_api_a_claim":
        rows.append((
            "class:mc-status-warn",
            trim(
                f"     {supervisor_descriptor.get('chain_reason', 'API-B 已转交，等待认领')}",
                inner_width,
            ),
        ))
    elif focus_stage == "running_on_other_api_a":
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
        rows.append(("class:mc-status-active", "  API-A 自主执行面工作中"))
    elif focus_stage == "local_claimed_waiting_first_turn":
        rows.append(("class:mc-body-accent", "  已认领链路项，等待首个回合"))
    elif focus_stage == "local_claimed_waiting_writeback":
        rows.append(("class:mc-body-accent", "  本轮结束，写回链路状态中"))
    elif focus_stage == "waiting_api_a_claim":
        desc = supervisor_descriptor.get("activity_text", "API-A 认领后执行，结果写回 Mem")
        rows.append(("class:mc-body-text", f"  {desc}"))
    elif focus_stage == "running_on_other_api_a":
        desc = supervisor_descriptor.get("activity_text", "链路项在其他执行面运行中")
        rows.append(("class:mc-body-accent", f"  {desc}"))
    else:
        desc = supervisor_descriptor.get("activity_text", "API-B 判断、重排或读取中")
        rows.append(("class:mc-body-dim", f"  {desc}"))

    return rows


def _build_lease_row(
    gateway_state: Dict[str, Any],
    session_id: str,
    inner_width: int,
    trim: Any,
) -> list[tuple[str, str]]:
    """Show the executor lease — which session owns the execution face."""
    active = dict(gateway_state.get("active_cli_executor") or {})
    active_session_id = str(active.get("session_id") or "").strip()

    if not active_session_id:
        return [(
            "class:mc-body-dim",
            "  执行面: 无活跃 API-A 执行会话",
        )]

    current = session_id.strip()
    is_self = active_session_id == current
    lease_status = str(active.get("lease_status") or "").strip().lower()
    idle_seconds = int(active.get("idle_seconds") or 0)
    scene = str(active.get("scene") or "idle").strip() or "idle"

    if lease_status == "stale" or bool(active.get("is_stale")):
        owner = "本会话" if is_self else f"会话 {active_session_id[-8:]}"
        return [(
            "class:mc-status-error",
            trim(f"  执行面: {owner} 陈旧 · 静默 {idle_seconds}s · {scene}", inner_width),
        )]

    if is_self:
        return [(
            "class:mc-status-success",
            trim(f"  执行面: 本会话 · 静默 {idle_seconds}s · {scene}", inner_width),
        )]
    else:
        return [(
            "class:mc-status-warn",
            trim(f"  执行面: 会话 {active_session_id[-8:]} · 静默 {idle_seconds}s", inner_width),
        )]


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


def _build_footer_rows(inner_width: int, trim: Any) -> list[tuple[str, str]]:
    """Keybinding hints."""
    return [(
        "class:mc-key-hint",
        trim(f"  /auto-q 停用    /auto [focus] 激活    面板自动隐藏于空闲时", inner_width),
    )]


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

    # ── Resolve status label + style ──────────────────────────────────
    if focus_stage == "local_claimed_active":
        status_label, status_style_key = "执行中", "mc-status-active"
    elif focus_stage == "local_claimed_waiting_writeback":
        status_label, status_style_key = "等待回写", "mc-status-active"
    elif focus_stage == "local_claimed_waiting_first_turn":
        status_label, status_style_key = "已认领 · 待起跑", "mc-status-warn"
    elif state_ports.agent_running():
        status_label, status_style_key = "模型处理中", "mc-status-active"
    elif focus_stage == "waiting_api_a_claim":
        label = str(supervisor_descriptor.get("status_label") or "API-B 已转交")
        status_label, status_style_key = label, "mc-status-warn"
    elif focus_stage == "running_on_other_api_a":
        label = str(supervisor_descriptor.get("status_label") or "他处执行中")
        status_label, status_style_key = label, "mc-body-accent"
    else:
        label = str(supervisor_descriptor.get("status_label") or "API-B 判断中")
        status_label, status_style_key = label, "mc-body-dim"

    # ── Assemble rows ─────────────────────────────────────────────────
    rows: list[tuple[str, str]] = []

    # 1. Header bar
    rows.extend(_build_header_rows(session_id, status_label, status_style_key, focus_stage, inner_width, trim))

    # 2. API-B status
    api_b_rows = _build_api_b_status_rows(supervisor_state, inner_width, trim)
    if api_b_rows:
        rows.append(("", ""))  # spacer
        rows.extend(api_b_rows)

    # 3. Scheduler
    sched_rows = _build_scheduler_rows(scheduler_snapshot, inner_width, trim,
                                       events=state_ports.scheduler_events() if state_ports.scheduler_events else ())
    if sched_rows:
        rows.append(("", ""))  # spacer
        rows.extend(_section_divider("调度", inner_width, trim))
        rows.extend(sched_rows)

    # 4. Task
    task_rows = _build_task_rows(
        focus_task, focus_stage,
        state_ports.current_task(), state_ports.current_task_started_at(),
        supervisor_descriptor, supervisor_state,
        state_ports.execution_events(),
        inner_width, trim,
    )
    if task_rows:
        rows.append(("", ""))  # spacer
        rows.extend(_section_divider("链路项", inner_width, trim))
        rows.extend(task_rows)

    # 5. Flow
    flow_rows = _build_flow_rows(
        focus_stage, state_ports.agent_running(), state_ports.spinner_text(),
        supervisor_descriptor, inner_width, trim,
    )
    if flow_rows:
        rows.append(("", ""))  # spacer
        rows.extend(_section_divider("执行流", inner_width, trim))
        rows.extend(flow_rows)

    # 6. Lease
    lease_rows = _build_lease_row(gateway_state, session_id, inner_width, trim)
    rows.append(("", ""))  # spacer
    rows.extend(_section_divider("执行面", inner_width, trim))
    rows.extend(lease_rows)

    # 7. Timeline / events
    timeline_rows = _build_timeline_rows(
        state_ports.execution_events(), supervisor_state, inner_width, trim,
    )
    if timeline_rows:
        rows.append(("", ""))  # spacer
        rows.extend(_section_divider("最近事件", inner_width, trim))
        rows.extend(timeline_rows)

    # 8. Footer
    rows.append(("", ""))  # spacer
    rows.extend(_build_footer_rows(inner_width, trim))

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


# ═══════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════


def _join_fragments(fragments: list[tuple[str, str]], sep: str) -> str:
    """Join styled fragment texts with a separator (for inline use)."""
    return sep.join(text for _, text in fragments)


def build_autonomous_executor_lease_row(
    gateway_state: Dict[str, Any],
    inner_width: int,
    *,
    session_id: str,
    trim_status_bar_text: Any,
) -> tuple[str, str]:
    """Legacy single-row lease builder — kept for external callers."""
    rows = _build_lease_row(gateway_state, session_id, inner_width, trim_status_bar_text)
    if rows:
        return rows[0]
    return ("class:mc-body-dim", "执行面: 未知")


def resolve_autonomous_waiting_start_cause(
    events: list[Dict[str, Any]],
) -> tuple[str, str]:
    """Legacy helper — kept for external callers."""
    return _resolve_waiting_cause(events)

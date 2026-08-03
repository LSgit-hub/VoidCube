from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from dataclasses import dataclass
from typing import Any, Dict


@dataclass(frozen=True, slots=True)
class AutonomousPanelEventPorts:
    """State and formatting callbacks for autonomous panel events."""

    gate_active: Callable[[], bool]
    execution_events: Callable[[], list[dict[str, object]]]
    set_execution_events: Callable[[list[dict[str, object]]], None]
    trim_status_bar_text: Callable[[str, int], str]
    last_supervisor_event_key: Callable[[], str]
    set_last_supervisor_event_key: Callable[[str], None]


def append_autonomous_execution_event(
    *,
    event_ports: AutonomousPanelEventPorts,
    message: str,
    tone: str = "info",
    stage: str = "",
) -> None:
    """Record a short autonomous execution event for the foreground panel."""
    compact = " ".join(str(message or "").split()).strip()
    if not compact:
        return
    events = list(event_ports.execution_events() or [])
    events.append(
        {
            "at": datetime.now().strftime("%H:%M:%S"),
            "message": event_ports.trim_status_bar_text(compact, 96),
            "tone": str(tone or "info"),
            "stage": str(stage or "").strip().lower(),
        }
    )
    event_ports.set_execution_events(events[-6:])


def sync_autonomous_supervisor_event(
    state: Dict[str, Any],
    *,
    event_ports: AutonomousPanelEventPorts,
) -> None:
    """Mirror the supervisor's latest visible autonomous-chain activity into the panel."""
    if not event_ports.gate_active():
        return
    timeline = list(state.get("timeline") or [])
    if not timeline:
        return
    latest = dict(timeline[0] or {})
    event_key = "|".join(
        [
            str(latest.get("created_at") or latest.get("timestamp") or ""),
            str(latest.get("event_type") or latest.get("source") or ""),
            str(latest.get("summary") or latest.get("title") or ""),
        ]
    )
    if (
        not event_key
        or event_key == event_ports.last_supervisor_event_key()
    ):
        return
    event_ports.set_last_supervisor_event_key(event_key)
    raw_label = str(latest.get("event_type") or latest.get("source") or "supervisor").strip()
    label_map = {
        "task_decided": "链路裁决",
        "tasks_reviewed": "API-B 复核记录",
        "tasks_planned": "链路规划",
        "supervisor_activity": "监督活动",
    }
    label = label_map.get(raw_label, raw_label or "监督活动")
    summary = str(latest.get("summary") or latest.get("title") or "").strip()
    if summary:
        append_autonomous_execution_event(
            event_ports=event_ports,
            message=f"监督者{label}: {summary}",
            tone="info",
            stage="supervisor",
        )

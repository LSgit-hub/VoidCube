from __future__ import annotations

from datetime import datetime
from typing import Any, Dict


def append_autonomous_execution_event(
    host: Any,
    message: str,
    *,
    tone: str = "info",
    stage: str = "",
) -> None:
    """Record a short autonomous execution event for the foreground panel."""
    compact = " ".join(str(message or "").split()).strip()
    if not compact:
        return
    events = list(getattr(host, "_autonomous_execution_events", []) or [])
    events.append(
        {
            "at": datetime.now().strftime("%H:%M:%S"),
            "message": host._trim_status_bar_text(compact, 96),
            "tone": str(tone or "info"),
            "stage": str(stage or "").strip().lower(),
        }
    )
    host._autonomous_execution_events = events[-6:]


def sync_autonomous_supervisor_event(host: Any, state: Dict[str, Any]) -> None:
    """Mirror the supervisor's latest visible autonomous-chain activity into the panel."""
    if not getattr(host, "_autonomous_gate_active", False):
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
    if not event_key or event_key == getattr(host, "_autonomous_last_supervisor_event_key", ""):
        return
    host._autonomous_last_supervisor_event_key = event_key
    raw_label = str(latest.get("event_type") or latest.get("source") or "supervisor").strip()
    label_map = {
        "task_decided": "链路裁决",
        "tasks_reviewed": "批量复核",
        "tasks_planned": "链路规划",
        "supervisor_activity": "监督活动",
    }
    label = label_map.get(raw_label, raw_label or "监督活动")
    summary = str(latest.get("summary") or latest.get("title") or "").strip()
    if summary:
        append_autonomous_execution_event(
            host,
            f"监督者{label}: {summary}",
            tone="info",
            stage="supervisor",
        )


def autonomous_execution_panel_height(host: Any) -> int:
    if not getattr(host, "_autonomous_gate_active", False):
        return 0
    from VoidCube_cli.autonomous_panel import build_autonomous_execution_panel_rows

    return len(build_autonomous_execution_panel_rows(host))

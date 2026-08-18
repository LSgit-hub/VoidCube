"""Persistent activity adapters for the Supervisor UI runtime."""

from __future__ import annotations

import json
import logging
from collections import deque
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Collection, Deque, Dict, List, Optional
import uuid

from ...infrastructure.persistence.file_store import atomic_json_write


logger = logging.getLogger("supervisor")
JsonDict = Dict[str, Any]


@dataclass(frozen=True, slots=True)
class SupervisorUIActivityContext:
    """State and callbacks needed to record one UI activity event."""

    activity_path: Optional[Path]
    events: Deque[JsonDict]
    legal_scenes: Collection[str]
    record_history: Callable[[JsonDict], None]


def load_supervisor_ui_activity(
    *,
    path: Optional[Path],
    max_events: int,
) -> List[JsonDict]:
    if path is None or not path.exists():
        return []
    try:
        raw = json.loads(path.read_text(encoding="utf-8") or "{}")
    except Exception:
        return []
    events = raw.get("events") if isinstance(raw, dict) else None
    if not isinstance(events, list):
        return []
    normalized = [dict(event) for event in events if isinstance(event, dict)]
    return normalized[: max(int(max_events), 0)]


def persist_supervisor_ui_activity(
    *,
    path: Optional[Path],
    events: Optional[Deque[JsonDict]],
) -> None:
    if path is None or events is None:
        return
    payload = {
        "version": 1,
        "updated_at": datetime.utcnow().isoformat(),
        "events": list(events),
    }
    try:
        atomic_json_write(path, payload)
    except Exception:
        return


def clear_supervisor_ui_activity(
    *,
    path: Optional[Path],
    events: Optional[Deque[JsonDict]],
) -> None:
    if events is not None:
        events.clear()
    if path is not None:
        payload = {
            "version": 1,
            "updated_at": datetime.utcnow().isoformat(),
            "events": [],
        }
        try:
            atomic_json_write(path, payload)
        except Exception:
            return


def recent_supervisor_ui_activity(
    *,
    events: Optional[Deque[JsonDict]],
    limit: int = 20,
) -> List[JsonDict]:
    if events is None:
        return []
    return list(events)[: max(limit, 0)]


def latest_drive_candidate_snapshot(
    *,
    events: Optional[Deque[JsonDict]],
) -> List[JsonDict]:
    for event in recent_supervisor_ui_activity(events=events, limit=20):
        event_type = str(event.get("event_type") or "").strip().lower()
        metadata = dict(event.get("metadata") or {})
        if event_type == "endogenous_drive_idle":
            return []
        if event_type == "endogenous_drive_evaluated":
            candidates = metadata.get("candidates")
            if isinstance(candidates, list):
                return [dict(item) for item in candidates if isinstance(item, dict)]
        if event_type == "endogenous_drive_planned":
            tasks = metadata.get("tasks")
            if isinstance(tasks, list):
                return [dict(item) for item in tasks if isinstance(item, dict)]
    return []


def record_supervisor_ui_activity(
    *,
    context: SupervisorUIActivityContext,
    event_type: str,
    scene: str = "planning",
    summary: str = "",
    metadata: Optional[JsonDict] = None,
) -> JsonDict:
    if scene not in context.legal_scenes:
        logger.warning(
            "Refusing illegal supervisor scene=%r for event_type=%r; "
            "falling back to 'planning'. Legal supervisor scenes: %s",
            scene,
            event_type,
            sorted(context.legal_scenes),
        )
        scene = "planning"

    event = {
        "event_id": str(uuid.uuid4()),
        "event_type": event_type,
        "scene": scene,
        "summary": summary or event_type.replace("_", " "),
        "metadata": dict(metadata or {}),
        "recorded_at": datetime.utcnow().isoformat(),
    }
    context.events.appendleft(event)
    persist_supervisor_ui_activity(
        path=context.activity_path,
        events=context.events,
    )
    context.record_history(event)
    return event



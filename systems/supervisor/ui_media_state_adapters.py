"""Mutable media-state adapter for the Supervisor UI runtime."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Dict


JsonDict = Dict[str, Any]


@dataclass(frozen=True, slots=True)
class SupervisorUIMediaStateContext:
    """Callbacks that keep media state owned by the Supervisor runtime."""

    current_revision: int
    set_revision: Callable[[int], None]
    set_current_media: Callable[[JsonDict], None]


def enqueue_media_state(
    *,
    context: SupervisorUIMediaStateContext,
    media: JsonDict,
) -> JsonDict:
    current = dict(media)
    current.setdefault("auto_play", True)
    current.setdefault("type", "auto")
    current.setdefault("title", current.get("url", "未知"))
    current["_enqueued_at"] = datetime.now(timezone.utc).isoformat()
    revision = int(context.current_revision) + 1
    current["_revision"] = revision
    context.set_revision(revision)
    context.set_current_media(current)
    return current

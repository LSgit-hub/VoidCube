"""Mutable media-state adapter for the Supervisor UI runtime."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Deque, Dict, Optional
from uuid import uuid4


JsonDict = Dict[str, Any]


@dataclass(frozen=True, slots=True)
class SupervisorUIMediaStateContext:
    """Callbacks that keep media state owned by the Supervisor runtime."""

    current_revision: int
    current_media: Optional[JsonDict]
    media_queue: Deque[JsonDict]
    set_revision: Callable[[int], None]
    set_current_media: Callable[[Optional[JsonDict]], None]


def _stamp_current(
    *,
    context: SupervisorUIMediaStateContext,
    current: Optional[JsonDict],
) -> Optional[JsonDict]:
    revision = int(context.current_revision) + 1
    context.set_revision(revision)
    if current is not None:
        current = dict(current)
        current["_revision"] = revision
        current.setdefault("playback", "playing")
    context.set_current_media(current)
    return current


def _prepare_media(media: JsonDict) -> JsonDict:
    current = dict(media)
    current.pop("queue_mode", None)
    current.setdefault("auto_play", True)
    current.setdefault("type", "auto")
    current.setdefault("title", current.get("url", "未知"))
    current.setdefault("playback", "playing")
    current["media_id"] = str(current.get("media_id") or uuid4().hex)
    current["_enqueued_at"] = datetime.now(timezone.utc).isoformat()
    return current


def enqueue_media_state(
    *,
    context: SupervisorUIMediaStateContext,
    media: JsonDict,
    queue_mode: str = "replace",
) -> JsonDict:
    item = _prepare_media(media)
    if queue_mode == "enqueue" and context.current_media is not None:
        context.media_queue.append(item)
        # Emit a revision so connected UIs refresh their queue count without
        # restarting the currently playing item.
        _stamp_current(context=context, current=context.current_media)
        return item

    context.media_queue.clear()
    return _stamp_current(context=context, current=item) or item


def control_media_state(
    *,
    context: SupervisorUIMediaStateContext,
    action: str,
    media_id: str = "",
) -> Optional[JsonDict]:
    """Apply one canonical playback action and return the new current item."""

    current = context.current_media
    normalized_action = str(action or "").strip().lower()
    requested_id = str(media_id or "").strip()
    if requested_id and current and requested_id != current.get("media_id"):
        return current
    if normalized_action in {"stop", "clear"}:
        context.media_queue.clear()
        return _stamp_current(context=context, current=None)
    if current is None:
        return None
    if normalized_action in {"pause", "resume"}:
        updated = dict(current)
        updated["playback"] = "paused" if normalized_action == "pause" else "playing"
        return _stamp_current(context=context, current=updated)
    if normalized_action in {"next", "ended"}:
        next_item = context.media_queue.popleft() if context.media_queue else None
        return _stamp_current(context=context, current=next_item)
    raise ValueError(f"unsupported media action: {normalized_action}")

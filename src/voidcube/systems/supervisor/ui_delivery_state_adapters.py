"""Persistent state for Agent-delivered artifacts shown in the Supervisor UI."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Deque, Dict, Optional
from uuid import uuid4


JsonDict = Dict[str, Any]


def load_delivery_state(path: Any) -> tuple[str, list[JsonDict], int]:
    """Load the selected delivery, bounded history, and revision."""
    import json

    try:
        if path is None or not path.exists():
            return "", [], 0
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            return "", [], 0
        raw_items = payload.get("items")
        items = (
            [dict(item) for item in raw_items if isinstance(item, dict)]
            if isinstance(raw_items, list)
            else []
        )
        selected_id = str(payload.get("selected_id") or "")
        if selected_id and not any(item.get("delivery_id") == selected_id for item in items):
            selected_id = ""
        return selected_id, items, max(int(payload.get("revision") or 0), 0)
    except Exception:
        return "", [], 0


def persist_delivery_state(
    path: Any,
    *,
    selected_id: str,
    items: Deque[JsonDict],
    revision: int,
) -> None:
    if path is None:
        return
    from ...infrastructure.persistence.file_store import atomic_json_write

    try:
        atomic_json_write(
            path,
            {
                "version": 1,
                "revision": int(revision),
                "selected_id": str(selected_id or ""),
                "items": [dict(item) for item in items],
            },
        )
    except Exception:
        return


@dataclass(frozen=True, slots=True)
class SupervisorUIDeliveryStateContext:
    current_revision: int
    selected_id: str
    items: Deque[JsonDict]
    set_revision: Callable[[int], None]
    set_selected_id: Callable[[str], None]


def selected_delivery(context: SupervisorUIDeliveryStateContext) -> Optional[JsonDict]:
    if not context.selected_id:
        return None
    return next(
        (
            dict(item)
            for item in context.items
            if item.get("delivery_id") == context.selected_id
        ),
        None,
    )


def _bump_revision(context: SupervisorUIDeliveryStateContext) -> int:
    revision = int(context.current_revision) + 1
    context.set_revision(revision)
    return revision


def push_delivery_state(
    *, context: SupervisorUIDeliveryStateContext, delivery: JsonDict
) -> JsonDict:
    item = dict(delivery)
    item["delivery_id"] = str(item.get("delivery_id") or uuid4().hex)
    item["delivered_at"] = str(
        item.get("delivered_at") or datetime.now(timezone.utc).isoformat()
    )
    item["_revision"] = _bump_revision(context)
    context.items.appendleft(item)
    context.set_selected_id(item["delivery_id"])
    return dict(item)


def select_delivery_state(
    *, context: SupervisorUIDeliveryStateContext, delivery_id: str
) -> Optional[JsonDict]:
    requested_id = str(delivery_id or "").strip()
    selected = next(
        (item for item in context.items if item.get("delivery_id") == requested_id),
        None,
    )
    if selected is None:
        return selected_delivery(context)
    context.set_selected_id(requested_id)
    _bump_revision(context)
    return dict(selected)


def clear_delivery_state(*, context: SupervisorUIDeliveryStateContext) -> None:
    context.items.clear()
    context.set_selected_id("")
    _bump_revision(context)


__all__ = [
    "SupervisorUIDeliveryStateContext",
    "clear_delivery_state",
    "load_delivery_state",
    "persist_delivery_state",
    "push_delivery_state",
    "select_delivery_state",
    "selected_delivery",
]



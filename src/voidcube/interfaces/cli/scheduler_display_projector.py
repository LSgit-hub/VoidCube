"""Project shared scheduler events for terminal presentation adapters."""

from __future__ import annotations

from collections import deque
from collections.abc import Callable, Mapping, Sequence
from threading import RLock
from typing import Any

from ...domain.contracts.scheduler import SchedulerEvent, SchedulerSnapshot


class SchedulerDisplayProjector:
    """Own the display-facing event history without owning scheduler state."""

    def __init__(self, *, max_events: int = 32) -> None:
        self._lock = RLock()
        self._events: deque[SchedulerEvent] = deque(maxlen=max(1, int(max_events)))

    def accept(self, event: SchedulerEvent) -> None:
        if not isinstance(event, SchedulerEvent):
            raise TypeError("event must be a SchedulerEvent")
        with self._lock:
            self._events.append(event)

    def events(self) -> tuple[SchedulerEvent, ...]:
        with self._lock:
            return tuple(self._events)

    def event_dicts(self) -> tuple[dict[str, Any], ...]:
        return tuple(event.to_dict() for event in self.events())

    def latest(self) -> SchedulerEvent | None:
        with self._lock:
            return self._events[-1] if self._events else None

    def snapshot(self, provider: Callable[[], SchedulerSnapshot]) -> SchedulerSnapshot:
        """Read the authoritative snapshot through an explicit port."""
        snapshot = provider()
        if not isinstance(snapshot, SchedulerSnapshot):
            raise TypeError("scheduler snapshot provider returned an invalid value")
        return snapshot

    def presentation(
        self,
        provider: Callable[[], SchedulerSnapshot],
    ) -> Mapping[str, Any]:
        """Return a compact, non-sensitive projection for TUI adapters."""
        snapshot = self.snapshot(provider)
        latest = self.latest()
        active = snapshot.active
        return {
            "active": active.to_dict() if active else None,
            "queued_count": len(snapshot.queued),
            "autonomous_gate": snapshot.autonomous_gate,
            "blocked_reason": snapshot.blocked_reason,
            "latest_event": latest.to_dict() if latest else None,
        }


__all__ = ["SchedulerDisplayProjector"]

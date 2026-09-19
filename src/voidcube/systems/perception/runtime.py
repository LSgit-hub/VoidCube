"""Explicit lifecycle owner for the read-only local perception loop."""

from __future__ import annotations

from dataclasses import dataclass
from functools import wraps
from threading import RLock
from datetime import datetime, timezone
from typing import Any

from .loop import LocalPerceptionLoop, PerceptionStepResult
from .timeline_store import TimelineStore
from .summary import LocalTimelineSummarizer


def _serialized(method):
    @wraps(method)
    def call(self, *args, **kwargs):
        with self._lock:
            return method(self, *args, **kwargs)
    return call


@dataclass(frozen=True, slots=True)
class PerceptionRuntimeStatus:
    state: str
    authorized: bool
    samples: int
    analyzed: int
    persisted_segments: int
    dropped_records: int
    last_observed_at: str | None


class PerceptionRuntime:
    """Own lifecycle and text-only segment persistence without spawning threads."""

    def __init__(self, loop: LocalPerceptionLoop, *, store: TimelineStore | None = None,
                 summarizer: LocalTimelineSummarizer | None = None) -> None:
        self._lock = RLock()
        self._pending_commit = None
        self.loop = loop
        self.store = store
        self.summarizer = summarizer
        self._state = "stopped"
        self._authorized = False
        self._samples = 0
        self._analyzed = 0
        self._persisted_segments = 0
        self._last_observed_at: datetime | None = None

    @_serialized
    def authorize(self) -> None:
        """Record explicit user consent; does not start capture."""
        self._authorized = True

    @_serialized
    def revoke_authorization(self, *, clear: bool = True) -> None:
        self._authorized = False
        if clear:
            self.clear()
        self._state = "stopped"

    @_serialized
    def start(self) -> None:
        if self._authorized and self._state == "stopped":
            self._state = "running"

    @_serialized
    def pause(self) -> None:
        if self._state == "running":
            self._state = "paused"

    @_serialized
    def resume(self) -> None:
        if self._state == "paused":
            self._state = "running"

    @_serialized
    def stop(self, *, flush: bool = True) -> None:
        self._state = "stopped"
        if flush:
            self.flush()

    @_serialized
    def clear(self) -> None:
        self._pending_commit = None
        self.loop.clear()
        self._samples = 0
        self._analyzed = 0
        self._persisted_segments = 0
        self._last_observed_at = None

    @_serialized
    def step(self) -> PerceptionStepResult | None:
        if self._state != "running":
            return None
        self._commit_pending()
        result = self.loop.step()
        self._samples += 1
        if result.record is not None:
            self._analyzed += 1
            self._last_observed_at = result.record.observed_at
        self._persist(result.closed_segments)
        return result

    @_serialized
    def flush(self) -> None:
        self._commit_pending()
        segment = self.loop.flush()
        if segment is not None:
            self._persist((segment,))

    @_serialized
    def status(self) -> PerceptionRuntimeStatus:
        return PerceptionRuntimeStatus(
            state=self._state,
            authorized=self._authorized,
            samples=self._samples,
            analyzed=self._analyzed,
            persisted_segments=self._persisted_segments,
            dropped_records=self.loop.buffer.dropped_records,
            last_observed_at=(
                self._last_observed_at.astimezone(timezone.utc).isoformat()
                if self._last_observed_at is not None
                else None
            ),
        )

    def _persist(self, segments: tuple[Any, ...]) -> None:
        for segment in segments:
            records = self.loop.segmenter.take_last_closed_records()
            if self.store is not None:
                self._pending_commit = (segment, records)
                self._commit_pending()

    def _commit_pending(self) -> None:
        if self._pending_commit is None:
            return
        segment, records = self._pending_commit
        if self.summarizer is not None and records:
            segment = self.summarizer.summarize(
                records, segment_id=segment.segment_id, provisional=segment.provisional,
            )
            self._pending_commit = (segment, ())
        self._persisted_segments += int(self.store.put(segment))
        self._pending_commit = None


def build_default_perception_runtime(
    store_path: str,
    *,
    monitor: int = 1,
    roi: dict[str, int] | None = None,
    capture_session_id: str = "",
    screen_parser: Any | None = None,
) -> PerceptionRuntime:
    """Assemble the explicit local MVP runtime; it remains stopped initially."""
    from .loop import build_default_local_perception_loop

    loop = build_default_local_perception_loop(
        monitor=monitor,
        roi=roi,
        capture_session_id=capture_session_id,
        screen_parser=screen_parser,
    )
    return PerceptionRuntime(
        loop,
        store=TimelineStore(store_path),
        summarizer=LocalTimelineSummarizer(),
    )


__all__ = ["PerceptionRuntime", "PerceptionRuntimeStatus", "build_default_perception_runtime"]

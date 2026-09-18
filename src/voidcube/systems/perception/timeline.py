"""Bounded perception memory and deterministic timeline segmentation."""

from __future__ import annotations

from collections import deque
from datetime import datetime, timedelta, timezone
from typing import Iterable

from .models import PerceptionRecord, SceneState, TimelineSegment


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


class PerceptionBuffer:
    """A bounded first-tier queue; it never writes screenshots to disk."""

    def __init__(self, *, max_records: int = 120) -> None:
        self.max_records = max(1, int(max_records))
        self._records: deque[PerceptionRecord] = deque(maxlen=self.max_records)
        self._dropped_records = 0
        self._scene = SceneState()

    @property
    def dropped_records(self) -> int:
        return self._dropped_records

    def append(self, record: PerceptionRecord) -> None:
        if len(self._records) >= self.max_records:
            self._dropped_records += 1
        self._records.append(record)
        self._scene = SceneState(
            scene=record.scene,
            application=record.application,
            window_title=record.window_title,
            observed_at=record.observed_at,
            last_change_at=record.observed_at,
            confidence=record.confidence,
            status="fresh",
            sequence=record.sequence,
        )

    def records(self) -> tuple[PerceptionRecord, ...]:
        return tuple(self._records)

    def latest(self) -> PerceptionRecord | None:
        return self._records[-1] if self._records else None

    def scene_state(self, *, now: datetime | None = None, stale_after_seconds: float = 10.0) -> SceneState:
        if self._scene.observed_at is None:
            return self._scene
        current = _aware(now or datetime.now(timezone.utc))
        age = max(0.0, (current - _aware(self._scene.observed_at)).total_seconds())
        self._scene.status = "stale" if age > max(0.0, stale_after_seconds) else "fresh"
        self._scene.age_ms = int(age * 1000)
        return self._scene

    def clear(self) -> None:
        self._records.clear()
        self._scene = SceneState()
        self._dropped_records = 0


class TimelineSegmenter:
    """Close fixed-duration or scene-boundary segments from records."""

    def __init__(self, *, segment_seconds: int = 300, min_scene_seconds: int = 30) -> None:
        self.segment_seconds = max(1, int(segment_seconds))
        self.min_scene_seconds = max(0, int(min_scene_seconds))
        self._pending: list[PerceptionRecord] = []

    @property
    def pending_records(self) -> tuple[PerceptionRecord, ...]:
        return tuple(self._pending)

    def append(self, record: PerceptionRecord) -> list[TimelineSegment]:
        if not self._pending:
            self._pending.append(record)
            return []
        first = self._pending[0]
        elapsed = (_aware(record.observed_at) - _aware(first.observed_at)).total_seconds()
        scene_changed = record.scene != first.scene or record.application != first.application
        if scene_changed and elapsed >= self.min_scene_seconds:
            segment = self._close(provisional=False)
            self._pending.append(record)
            return [segment]
        self._pending.append(record)
        if elapsed >= self.segment_seconds:
            return [self._close(provisional=False)]
        return []

    def flush(self, *, provisional: bool = True) -> TimelineSegment | None:
        if not self._pending:
            return None
        return self._close(provisional=provisional)

    def _close(self, *, provisional: bool) -> TimelineSegment:
        records = self._pending
        self._pending = []
        first, last = records[0], records[-1]
        source_ids = tuple(record.record_id for record in records)
        scenes = [record.scene for record in records if record.scene]
        applications = [record.application for record in records if record.application]
        summary = next((record.summary for record in reversed(records) if record.summary), "")
        uncertainties = tuple(
            item
            for record in records
            for item in record.uncertainties
        )
        gaps = tuple(
            item
            for record in records
            for item in record.coverage_gaps
        )
        confidence = sum(record.confidence for record in records) / len(records)
        return TimelineSegment(
            segment_id=f"segment:{first.record_id}:{last.record_id}",
            start_at=first.observed_at,
            end_at=last.observed_at,
            scene=max(set(scenes), key=scenes.count) if scenes else "unknown",
            application=max(set(applications), key=applications.count) if applications else "",
            summary=summary,
            source_record_ids=source_ids,
            source_count=len(records),
            confidence=confidence,
            unknowns=uncertainties,
            coverage_gaps=gaps,
            provisional=provisional,
        )


__all__ = ["PerceptionBuffer", "TimelineSegmenter"]

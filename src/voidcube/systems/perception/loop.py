"""Small orchestration layer for one local perception sampling step."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from .capture import ScreenFrame
from .frame_change import FrameChangeDetector, FrameChangeResult
from .models import PerceptionRecord, SceneState, TimelineSegment
from .timeline import PerceptionBuffer, TimelineSegmenter


class FrameSource(Protocol):
    """Read-only source of screen frames."""

    def capture(self) -> ScreenFrame:
        ...


class RecordAnalyzer(Protocol):
    """Analyze one changed frame into a perception record."""

    def analyze(self, image_bytes: bytes, **kwargs: Any) -> PerceptionRecord:
        ...


@dataclass(frozen=True, slots=True)
class PerceptionStepResult:
    """Observable result of one sampling step."""

    captured: bool
    changed: bool
    change: FrameChangeResult
    record: PerceptionRecord | None
    closed_segments: tuple[TimelineSegment, ...]
    scene: SceneState


class LocalPerceptionLoop:
    """Connect a read-only frame source to local analysis and memory tiers.

    The loop intentionally performs one bounded step at a time.  A scheduler
    can call :meth:`step` at a configured cadence and apply its own backoff;
    this class never sleeps, starts threads, uploads frames, or invokes API-B.
    """

    def __init__(
        self,
        frame_source: FrameSource,
        analyzer: RecordAnalyzer,
        *,
        change_detector: FrameChangeDetector | None = None,
        buffer: PerceptionBuffer | None = None,
        segmenter: TimelineSegmenter | None = None,
        capture_session_id: str = "",
    ) -> None:
        self.frame_source = frame_source
        self.analyzer = analyzer
        self.change_detector = change_detector or FrameChangeDetector()
        self.buffer = buffer or PerceptionBuffer()
        self.segmenter = segmenter or TimelineSegmenter()
        self.capture_session_id = capture_session_id
        self._sequence = 0

    def step(self) -> PerceptionStepResult:
        frame = self.frame_source.capture()
        change = self.change_detector.observe(frame.pixels)
        self._sequence += 1
        if not change.changed:
            return PerceptionStepResult(
                captured=True,
                changed=False,
                change=change,
                record=None,
                closed_segments=(),
                scene=self.buffer.scene_state(now=frame.captured_at),
            )

        record = self.analyzer.analyze(
            frame.to_png(),
            record_id=f"perception:{self.capture_session_id or 'session'}:{self._sequence}",
            observed_at=frame.captured_at,
            capture_session_id=self.capture_session_id,
            sequence=self._sequence,
        )
        self.buffer.append(record)
        closed = tuple(self.segmenter.append(record))
        return PerceptionStepResult(
            captured=True,
            changed=True,
            change=change,
            record=record,
            closed_segments=closed,
            scene=self.buffer.scene_state(now=frame.captured_at),
        )

    def flush(self) -> TimelineSegment | None:
        """Close the current in-memory tail as a provisional segment."""

        return self.segmenter.flush(provisional=True)

    def clear(self) -> None:
        """Clear first-tier records and reset frame comparison state."""

        self.change_detector.reset()
        self.buffer.clear()
        self.segmenter.flush(provisional=False)
        self._sequence = 0


__all__ = [
    "FrameSource",
    "LocalPerceptionLoop",
    "PerceptionStepResult",
    "RecordAnalyzer",
]

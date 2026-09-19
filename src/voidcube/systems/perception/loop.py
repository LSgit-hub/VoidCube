"""Small orchestration layer for one local perception sampling step."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol
from uuid import uuid4

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

    def analyze(self, image_bytes: bytes, **kwargs: Any) -> Any:
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
    model: str = ""
    escalated: bool = False
    escalation_reason: str = ""


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
        self.capture_session_id = capture_session_id or uuid4().hex
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

        try:
            analysis = self.analyzer.analyze(
                frame.to_png(),
                record_id=f"perception:{self.capture_session_id or 'session'}:{self._sequence}",
                observed_at=frame.captured_at,
                capture_session_id=self.capture_session_id,
                sequence=self._sequence,
            )
        except Exception:
            self.change_detector.reset()
            raise
        record, model, escalated, escalation_reason = _normalize_analysis(analysis)
        self.buffer.append(record)
        closed = tuple(self.segmenter.append(record))
        return PerceptionStepResult(
            captured=True,
            changed=True,
            change=change,
            record=record,
            closed_segments=closed,
            scene=self.buffer.scene_state(now=frame.captured_at),
            model=model,
            escalated=escalated,
            escalation_reason=escalation_reason,
        )

    def flush(self) -> TimelineSegment | None:
        """Close the current in-memory tail as a provisional segment."""

        return self.segmenter.flush(provisional=True)

    def clear(self) -> None:
        """Clear first-tier records and reset frame comparison state."""

        self.change_detector.reset()
        self.buffer.clear()
        self.segmenter.clear()
        self.capture_session_id = uuid4().hex
        self._sequence = 0


def build_default_local_perception_loop(
    *,
    monitor: int = 1,
    roi: dict[str, int] | None = None,
    capture_session_id: str = "",
    screen_parser: Any | None = None,
) -> LocalPerceptionLoop:
    """Build the production MVP chain without starting capture or threads."""

    from .adaptive import AdaptiveLocalVisionAnalyzer
    from .capture import MssScreenCapture

    return LocalPerceptionLoop(
        MssScreenCapture(monitor=monitor, roi=roi),
        AdaptiveLocalVisionAnalyzer(screen_parser=screen_parser),
        capture_session_id=capture_session_id,
    )


__all__ = [
    "FrameSource",
    "build_default_local_perception_loop",
    "LocalPerceptionLoop",
    "PerceptionStepResult",
    "RecordAnalyzer",
]


def _normalize_analysis(
    analysis: Any,
) -> tuple[PerceptionRecord, str, bool, str]:
    """Accept a plain record or an AdaptiveVisionResult without coupling layers."""

    if isinstance(analysis, PerceptionRecord):
        return analysis, "", False, ""
    record = getattr(analysis, "record", None)
    if not isinstance(record, PerceptionRecord):
        raise TypeError("perception analyzer must return PerceptionRecord or an adaptive result")
    return (
        record,
        str(getattr(analysis, "model", "") or ""),
        bool(getattr(analysis, "escalated", False)),
        str(getattr(analysis, "escalation_reason", "") or ""),
    )

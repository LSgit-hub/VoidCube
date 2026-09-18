"""Local, read-only computer perception primitives.

The package deliberately contains no screen-capture backend, model download,
network client, or computer-control tool.  Backends such as ``mss`` and
OmniParser can be adapted to these contracts without changing the timeline or
privacy boundaries.
"""

from .frame_change import FrameChangeDetector, FrameChangeResult
from .capture import MssScreenCapture, ScreenCaptureUnavailable, ScreenFrame
from .models import (
    PerceptionEvent,
    PerceptionRecord,
    SceneState,
    TimelineSegment,
)
from .timeline import PerceptionBuffer, TimelineSegmenter
from .vision import LocalVisionAnalyzer, LocalVisionConfig, VisionAnalysisError
from .loop import LocalPerceptionLoop, PerceptionStepResult
from .summary import LocalTimelineSummarizer, TimelineSummaryError

__all__ = [
    "FrameChangeDetector",
    "FrameChangeResult",
    "LocalVisionAnalyzer",
    "LocalVisionConfig",
    "LocalTimelineSummarizer",
    "LocalPerceptionLoop",
    "MssScreenCapture",
    "PerceptionBuffer",
    "PerceptionEvent",
    "PerceptionRecord",
    "SceneState",
    "ScreenCaptureUnavailable",
    "ScreenFrame",
    "TimelineSegment",
    "TimelineSegmenter",
    "TimelineSummaryError",
    "PerceptionStepResult",
    "VisionAnalysisError",
]

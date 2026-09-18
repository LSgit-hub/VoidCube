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
from .loop import LocalPerceptionLoop, PerceptionStepResult, build_default_local_perception_loop
from .summary import LocalTimelineSummarizer, TimelineSummaryError
from .adaptive import AdaptiveLocalVisionAnalyzer, AdaptiveVisionConfig, AdaptiveVisionResult
from .screen_parser import (
    OmniParserAdapter,
    ParsedScreen,
    ScreenParserUnavailable,
    normalize_screen_parser_output,
)
from .timeline_store import TimelineStore
from .runtime import PerceptionRuntime, PerceptionRuntimeStatus, build_default_perception_runtime
from .context import build_perception_context
from .query import PerceptionQueryService
from .worker import PerceptionWorker, PerceptionWorkerStatus

__all__ = [
    "FrameChangeDetector",
    "FrameChangeResult",
    "AdaptiveLocalVisionAnalyzer",
    "AdaptiveVisionConfig",
    "AdaptiveVisionResult",
    "OmniParserAdapter",
    "ParsedScreen",
    "ScreenParserUnavailable",
    "normalize_screen_parser_output",
    "TimelineStore",
    "PerceptionRuntime",
    "PerceptionRuntimeStatus",
    "build_default_perception_runtime",
    "build_perception_context",
    "PerceptionQueryService",
    "PerceptionWorker",
    "PerceptionWorkerStatus",
    "build_default_local_perception_loop",
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

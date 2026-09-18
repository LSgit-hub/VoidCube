"""Local model escalation policy for cost-aware screen perception."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from .models import PerceptionRecord
from .vision import LocalVisionAnalyzer, LocalVisionConfig
from .screen_parser import ScreenParser, ScreenParserUnavailable


@dataclass(frozen=True, slots=True)
class AdaptiveVisionConfig:
    """Policy for choosing a light or deep local model."""

    fast_model: str = "MiniCPM-V4.6:1b"
    deep_model: str = "qwen3.5:4b"
    confidence_threshold: float = 0.65
    escalate_unknown_scene: bool = True
    escalate_missing_summary: bool = True
    deep_timeout_seconds: float = 180.0

    @classmethod
    def from_runtime(cls) -> "AdaptiveVisionConfig":
        """Read optional local perception settings without requiring config keys."""
        values: Mapping[str, Any] = {}
        try:
            from ...infrastructure.config.configuration import load_config

            config = load_config()
            section = config.get("perception") if isinstance(config, Mapping) else None
            values = section if isinstance(section, Mapping) else {}
        except Exception:
            pass
        fast = values.get("fast_model") or LocalVisionConfig.from_runtime().model
        deep = values.get("deep_model") or "qwen3.5:4b"
        try:
            threshold = float(values.get("confidence_threshold", 0.65))
        except (TypeError, ValueError):
            threshold = 0.65
        return cls(
            fast_model=str(fast),
            deep_model=str(deep),
            confidence_threshold=max(0.0, min(1.0, threshold)),
            escalate_unknown_scene=bool(values.get("escalate_unknown_scene", True)),
            escalate_missing_summary=bool(values.get("escalate_missing_summary", True)),
            deep_timeout_seconds=float(values.get("deep_timeout_seconds", 180.0)),
        )


@dataclass(frozen=True, slots=True)
class AdaptiveVisionResult:
    record: PerceptionRecord
    model: str
    escalated: bool
    escalation_reason: str


class AdaptiveLocalVisionAnalyzer:
    """Run fast local perception first and optionally retry with a deep model."""

    def __init__(
        self,
        config: AdaptiveVisionConfig | None = None,
        *,
        fast_analyzer: LocalVisionAnalyzer | None = None,
        deep_analyzer: LocalVisionAnalyzer | None = None,
        screen_parser: ScreenParser | None = None,
    ) -> None:
        self.config = config or AdaptiveVisionConfig.from_runtime()
        self.fast_analyzer = fast_analyzer or LocalVisionAnalyzer(
            LocalVisionConfig(model=self.config.fast_model)
        )
        self.deep_analyzer = deep_analyzer or LocalVisionAnalyzer(
            LocalVisionConfig(
                model=self.config.deep_model,
                timeout_seconds=self.config.deep_timeout_seconds,
            )
        )
        self.screen_parser = screen_parser

    def analyze(self, image_bytes: bytes, **kwargs: Any) -> AdaptiveVisionResult:
        deep_analysis = bool(kwargs.pop("deep_analysis", False))
        parser_context: Mapping[str, Any] | None = None
        if self.screen_parser is not None:
            try:
                parsed = self.screen_parser.parse(image_bytes)
                parser_context = parsed.as_dict() if hasattr(parsed, "as_dict") else dict(parsed)
                kwargs["parser_context"] = parser_context
            except ScreenParserUnavailable:
                parser_context = None
        record = self.fast_analyzer.analyze(image_bytes, **kwargs)
        reason = self._escalation_reason(record, deep_analysis)
        if not reason:
            return AdaptiveVisionResult(record, self.config.fast_model, False, "")
        deep_record = self.deep_analyzer.analyze(image_bytes, **kwargs)
        return AdaptiveVisionResult(deep_record, self.config.deep_model, True, reason)

    def _escalation_reason(self, record: PerceptionRecord, deep_analysis: bool) -> str:
        if deep_analysis:
            return "user_requested_deep_analysis"
        if record.confidence < self.config.confidence_threshold:
            return "fast_model_low_confidence"
        if self.config.escalate_unknown_scene and record.scene in {"", "unknown"}:
            return "fast_model_unknown_scene"
        if self.config.escalate_missing_summary and not record.summary:
            return "fast_model_missing_summary"
        return ""


__all__ = ["AdaptiveLocalVisionAnalyzer", "AdaptiveVisionConfig", "AdaptiveVisionResult"]

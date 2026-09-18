from __future__ import annotations

from datetime import datetime, timezone

from voidcube.systems.perception import (
    AdaptiveLocalVisionAnalyzer,
    AdaptiveVisionConfig,
    LocalPerceptionLoop,
    LocalVisionAnalyzer,
    LocalVisionConfig,
    ScreenFrame,
)


class _Source:
    def capture(self) -> ScreenFrame:
        return ScreenFrame(
            pixels=b"a" * 128,
            width=32,
            height=1,
            monitor=1,
            captured_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        )


def test_loop_projects_adaptive_model_metadata() -> None:
    fast = LocalVisionAnalyzer(
        LocalVisionConfig(model="MiniCPM-V4.6:1b"),
        completion=lambda **_: '{"scene":"unknown","confidence":0.1}',
    )
    deep = LocalVisionAnalyzer(
        LocalVisionConfig(model="qwen3.5:4b"),
        completion=lambda **_: '{"scene":"editing_code","summary":"深度分析","confidence":0.9}',
    )
    analyzer = AdaptiveLocalVisionAnalyzer(
        AdaptiveVisionConfig(), fast_analyzer=fast, deep_analyzer=deep
    )
    result = LocalPerceptionLoop(_Source(), analyzer).step()

    assert result.model == "qwen3.5:4b"
    assert result.escalated is True
    assert result.escalation_reason == "fast_model_low_confidence"
    assert result.record is not None
    assert result.record.summary == "深度分析"

from __future__ import annotations

from datetime import datetime, timezone

from voidcube.systems.perception import (
    AdaptiveLocalVisionAnalyzer,
    AdaptiveVisionConfig,
    LocalVisionAnalyzer,
    LocalVisionConfig,
    PerceptionRecord,
    OmniParserAdapter,
)


def _record(record_id: str, *, confidence: float, scene: str, summary: str) -> PerceptionRecord:
    return PerceptionRecord(
        record_id=record_id,
        observed_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        scene=scene,
        summary=summary,
        confidence=confidence,
    )


def test_adaptive_analyzer_keeps_fast_result_when_confident() -> None:
    fast = LocalVisionAnalyzer(completion=lambda **_: '{"scene":"editing_code","summary":"代码","confidence":0.9}')
    deep = LocalVisionAnalyzer(completion=lambda **_: '{"scene":"deep","summary":"深度","confidence":1}')
    analyzer = AdaptiveLocalVisionAnalyzer(
        AdaptiveVisionConfig(fast_model="MiniCPM-V4.6:1b", deep_model="qwen3.5:4b"),
        fast_analyzer=fast,
        deep_analyzer=deep,
    )

    result = analyzer.analyze(b"frame", record_id="r1")

    assert result.escalated is False
    assert result.model == "MiniCPM-V4.6:1b"
    assert result.record.scene == "editing_code"


def test_adaptive_analyzer_escalates_low_confidence_to_deep_model() -> None:
    fast = LocalVisionAnalyzer(completion=lambda **_: '{"scene":"unknown","confidence":0.2}')
    deep = LocalVisionAnalyzer(completion=lambda **_: '{"scene":"watching_video","summary":"剧情","confidence":0.9}')
    analyzer = AdaptiveLocalVisionAnalyzer(
        AdaptiveVisionConfig(confidence_threshold=0.65),
        fast_analyzer=fast,
        deep_analyzer=deep,
    )

    result = analyzer.analyze(b"frame", record_id="r2")

    assert result.escalated is True
    assert result.escalation_reason == "fast_model_low_confidence"
    assert result.model == "qwen3.5:4b"
    assert result.record.summary == "剧情"


def test_adaptive_analyzer_can_escalate_on_explicit_user_request() -> None:
    calls: list[str] = []

    def completion(*, model: str, **_: object) -> str:
        calls.append(model)
        return '{"scene":"editing_code","summary":"分析结果","confidence":0.9}'

    analyzer = AdaptiveLocalVisionAnalyzer(
        AdaptiveVisionConfig(),
        fast_analyzer=LocalVisionAnalyzer(
            LocalVisionConfig(model="MiniCPM-V4.6:1b"), completion=completion
        ),
        deep_analyzer=LocalVisionAnalyzer(
            LocalVisionConfig(model="qwen3.5:4b"), completion=completion
        ),
    )

    result = analyzer.analyze(b"frame", record_id="r3", deep_analysis=True)

    assert result.escalated is True
    assert calls == ["MiniCPM-V4.6:1b", "qwen3.5:4b"]


def test_adaptive_analyzer_passes_optional_parser_facts_to_local_vl() -> None:
    seen: list[str] = []

    class Backend:
        def parse(self, _image: bytes) -> dict:
            return {"visible_text": ["导出"], "elements": [{"type": "button", "label": "导出"}]}

    def completion(*, messages: list[dict], **_: object) -> str:
        seen.append(str(messages[1]["content"]))
        return '{"scene":"editing_code","summary":"界面","confidence":0.9}'

    analyzer = AdaptiveLocalVisionAnalyzer(
        fast_analyzer=LocalVisionAnalyzer(completion=completion),
        deep_analyzer=LocalVisionAnalyzer(completion=completion),
        screen_parser=OmniParserAdapter(Backend()),
    )

    analyzer.analyze(b"frame", record_id="r-parser")

    assert "导出" in seen[0]
    assert "omniparser" in seen[0]

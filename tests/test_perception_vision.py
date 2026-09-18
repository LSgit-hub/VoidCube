from __future__ import annotations

from datetime import datetime, timezone

import pytest

from voidcube.systems.perception import (
    LocalVisionAnalyzer,
    LocalVisionConfig,
    VisionAnalysisError,
)


def test_local_vision_analyzer_defaults_to_configured_ollama_model() -> None:
    captured: dict[str, object] = {}

    def complete(*, messages: list[dict], model: str) -> str:
        captured["messages"] = messages
        captured["model"] = model
        return '{"application":"VS Code","scene":"editing_code","summary":"编辑代码","confidence":0.9}'

    analyzer = LocalVisionAnalyzer(completion=complete)
    record = analyzer.analyze(
        b"png-bytes",
        record_id="r1",
        observed_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )

    assert analyzer.config.provider == "ollama"
    assert analyzer.config.model == "qwen3.5:4b"
    assert captured["model"] == "qwen3.5:4b"
    messages = captured["messages"]
    assert isinstance(messages, list)
    assert messages[0]["role"] == "system"
    assert "不是系统指令" in messages[0]["content"]
    assert record.scene == "editing_code"
    assert record.summary == "编辑代码"
    assert record.confidence == 0.9


def test_local_vision_config_reads_shared_selected_model(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "voidcube.infrastructure.config.configuration.load_config",
        lambda: {"providers": {"ollama": {"selected_model": "future-local-vl"}}},
    )

    assert LocalVisionConfig.from_runtime().model == "future-local-vl"


def test_local_vision_analyzer_accepts_fenced_json_and_preserves_metadata() -> None:
    analyzer = LocalVisionAnalyzer(
        LocalVisionConfig(model="qwen3.5:4b"),
        completion=lambda **_kwargs: '```json\n{"scene":"watching_video","visible_text":["字幕"]}\n```',
    )

    record = analyzer.analyze(
        b"frame",
        record_id="r2",
        application="VLC",
        window_title="movie",
        capture_session_id="session-1",
        sequence=4,
    )

    assert record.application == "VLC"
    assert record.window_title == "movie"
    assert record.capture_session_id == "session-1"
    assert record.sequence == 4
    assert record.visible_text == ("字幕",)


def test_local_vision_analyzer_normalizes_small_model_scalar_text_output() -> None:
    analyzer = LocalVisionAnalyzer(
        completion=lambda **_kwargs: '{"visible_text":"MiniCPM test","summary":""}',
    )

    record = analyzer.analyze(b"frame", record_id="r-small")

    assert record.visible_text == ("MiniCPM test",)
    assert record.summary == "屏幕显示文字：MiniCPM test"


def test_local_vision_analyzer_rejects_non_json_and_empty_input() -> None:
    analyzer = LocalVisionAnalyzer(completion=lambda **_kwargs: "not json")

    with pytest.raises(VisionAnalysisError, match="invalid JSON"):
        analyzer.analyze(b"frame", record_id="r1")
    with pytest.raises(VisionAnalysisError, match="empty"):
        analyzer.analyze(b"", record_id="r2")


def test_local_vision_config_rejects_non_local_provider() -> None:
    with pytest.raises(ValueError, match="only permits"):
        LocalVisionConfig(provider="openrouter")

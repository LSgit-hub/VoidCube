from __future__ import annotations

from datetime import datetime, timezone

import pytest

from voidcube.systems.perception import (
    LocalTimelineSummarizer,
    PerceptionRecord,
    TimelineSummaryError,
)


def _record(record_id: str, second: int) -> PerceptionRecord:
    return PerceptionRecord(
        record_id=record_id,
        observed_at=datetime(2026, 1, 1, 0, 0, second, tzinfo=timezone.utc),
        source=("screen", "vl"),
        scene="editing_code",
        application="VS Code",
        summary=f"观察 {second}",
        confidence=0.8,
    )


def test_local_timeline_summarizer_builds_segment_from_structured_json() -> None:
    captured: dict[str, object] = {}

    def complete(*, messages: list[dict], model: str) -> str:
        captured.update(messages=messages, model=model)
        return '{"summary":"用户编辑代码","scene":"editing_code","application":"VS Code","key_events":["打开文件"],"facts":["看到代码"],"inferences":["可能在调试"],"unknowns":["是否保存"],"confidence":0.76}'

    segment = LocalTimelineSummarizer(completion=complete).summarize(
        [_record("r2", 2), _record("r1", 1)],
        segment_id="segment-1",
        provisional=True,
    )

    assert captured["model"] == "qwen3.5:4b"
    assert segment.source_record_ids == ("r1", "r2")
    assert segment.summary == "用户编辑代码"
    assert segment.inferences == ("可能在调试",)
    assert segment.provisional is True


def test_local_timeline_summarizer_rejects_empty_or_invalid_output() -> None:
    summarizer = LocalTimelineSummarizer(completion=lambda **_kwargs: "bad")
    with pytest.raises(TimelineSummaryError, match="empty"):
        summarizer.summarize([], segment_id="empty")
    with pytest.raises(TimelineSummaryError, match="not valid JSON"):
        summarizer.summarize([_record("r1", 1)], segment_id="bad")


def test_malformed_model_confidence_does_not_break_persistence_or_hide_gaps():
    record = _record("r1", 1)
    record.coverage_gaps = ("capture_failed",)
    summarizer = LocalTimelineSummarizer(completion=lambda **_: '{"summary":"observed","confidence":[]}')
    segment = summarizer.summarize([record], segment_id="s")
    assert segment.confidence == 0.0
    assert "summary_confidence_unavailable" in segment.unknowns
    assert segment.coverage_gaps == ("capture_failed",)

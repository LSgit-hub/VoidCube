from __future__ import annotations

from datetime import datetime, timezone
import pytest

from voidcube.systems.perception import (
    LocalPerceptionLoop,
    PerceptionRecord,
    PerceptionRuntime,
    ScreenFrame,
    TimelineStore,
    LocalTimelineSummarizer,
)


class _Source:
    def __init__(self) -> None:
        self.index = 0

    def capture(self) -> ScreenFrame:
        self.index += 1
        return ScreenFrame(
            bytes([self.index]) * 128,
            32,
            1,
            1,
            datetime(2026, 1, 1, 0, 0, self.index, tzinfo=timezone.utc),
        )


class _Analyzer:
    def analyze(self, _image: bytes, **kwargs: object) -> PerceptionRecord:
        return PerceptionRecord(
            record_id=str(kwargs["record_id"]),
            observed_at=kwargs["observed_at"],
            scene="editing_code",
            summary="编辑代码",
            confidence=0.8,
        )


def test_runtime_pause_stop_and_persists_closed_tail(tmp_path) -> None:
    loop = LocalPerceptionLoop(_Source(), _Analyzer())
    with TimelineStore(tmp_path / "timeline.sqlite3") as store:
        runtime = PerceptionRuntime(loop, store=store)
        runtime.authorize()
        assert runtime.step() is None
        runtime.start()
        assert runtime.step() is not None
        runtime.pause()
        assert runtime.step() is None
        runtime.resume()
        runtime.stop(flush=True)
        status = runtime.status()

        assert status.state == "stopped"
        assert status.samples == 1
        assert status.analyzed == 1
        assert status.persisted_segments == 1
        assert store.count() == 1


def test_runtime_clear_resets_counters_and_memory(tmp_path) -> None:
    runtime = PerceptionRuntime(LocalPerceptionLoop(_Source(), _Analyzer()))
    runtime.authorize()
    runtime.start()
    runtime.step()
    runtime.clear()

    status = runtime.status()
    assert status.state == "running"
    assert status.samples == 0
    assert status.analyzed == 0
    assert status.last_observed_at is None


def test_runtime_summarizes_closed_records_before_persisting(tmp_path) -> None:
    loop = LocalPerceptionLoop(_Source(), _Analyzer())
    summarizer = LocalTimelineSummarizer(
        completion=lambda **_: '{"summary":"压缩后的摘要","confidence":0.9}'
    )
    with TimelineStore(tmp_path / "summary.sqlite3") as store:
        runtime = PerceptionRuntime(loop, store=store, summarizer=summarizer)
        runtime.authorize()
        runtime.start()
        runtime.step()
        runtime.flush()
        assert store.query(
            start_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            end_at=datetime(2026, 1, 2, tzinfo=timezone.utc),
        )[0].summary == "压缩后的摘要"


def test_summary_failure_preserves_records_for_retry(tmp_path):
    attempts = []
    def complete(**kwargs):
        attempts.append(kwargs["messages"])
        if len(attempts) == 1:
            raise RuntimeError("temporary")
        return '{"summary":"retried"}'
    with TimelineStore(tmp_path / "retry.sqlite3") as store:
        runtime = PerceptionRuntime(LocalPerceptionLoop(_Source(), _Analyzer()), store=store,
                                    summarizer=LocalTimelineSummarizer(completion=complete))
        runtime.authorize()
        runtime.start()
        runtime.step()
        with pytest.raises(RuntimeError):
            runtime.stop()
        assert runtime.status().state == "stopped"
        assert store.count() == 0
        runtime.flush()
        assert store.count() == 1
        assert attempts[0] == attempts[1]

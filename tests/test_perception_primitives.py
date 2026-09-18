from __future__ import annotations

from datetime import datetime, timedelta, timezone

from voidcube.systems.perception import (
    FrameChangeDetector,
    PerceptionBuffer,
    PerceptionRecord,
    TimelineSegmenter,
)


def _record(
    record_id: str,
    seconds: int,
    *,
    scene: str = "editing_code",
    application: str = "VS Code",
    summary: str = "",
    confidence: float = 0.8,
) -> PerceptionRecord:
    return PerceptionRecord(
        record_id=record_id,
        observed_at=datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(seconds=seconds),
        source=("screen", "vl"),
        scene=scene,
        application=application,
        summary=summary,
        confidence=confidence,
    )


def test_frame_change_detector_is_stable_for_identical_frames() -> None:
    detector = FrameChangeDetector(threshold=0.2, sample_bytes=64)

    first = detector.observe(b"a" * 256)
    second = detector.observe(b"a" * 256)

    assert first.changed is True
    assert second.changed is False
    assert second.changed_ratio == 0.0
    assert first.digest == second.digest


def test_frame_change_detector_reports_a_small_changed_region() -> None:
    detector = FrameChangeDetector(threshold=0.1, sample_bytes=100)
    detector.observe(b"a" * 100)

    result = detector.observe(b"a" * 90 + b"b" * 10)

    assert result.changed is True
    assert result.changed_ratio == 0.1


def test_perception_buffer_is_bounded_and_projects_latest_scene() -> None:
    buffer = PerceptionBuffer(max_records=2)
    buffer.append(_record("r1", 0))
    buffer.append(_record("r2", 1, scene="watching_video", application="VLC"))
    buffer.append(_record("r3", 2, scene="watching_video", application="VLC"))

    assert [record.record_id for record in buffer.records()] == ["r2", "r3"]
    assert buffer.dropped_records == 1
    state = buffer.scene_state(now=datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(seconds=3))
    assert state.scene == "watching_video"
    assert state.application == "VLC"
    assert state.status == "fresh"


def test_perception_buffer_marks_old_scene_stale() -> None:
    buffer = PerceptionBuffer()
    buffer.append(_record("r1", 0))

    state = buffer.scene_state(
        now=datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(seconds=11),
        stale_after_seconds=10,
    )

    assert state.status == "stale"
    assert state.age_ms == 11000


def test_timeline_segmenter_closes_fixed_duration_segment() -> None:
    segmenter = TimelineSegmenter(segment_seconds=10, min_scene_seconds=30)

    assert segmenter.append(_record("r1", 0)) == []
    assert segmenter.append(_record("r2", 5)) == []
    segments = segmenter.append(_record("r3", 10, summary="用户编辑代码"))

    assert len(segments) == 1
    assert segments[0].source_record_ids == ("r1", "r2", "r3")
    assert segments[0].summary == "用户编辑代码"
    assert segments[0].provisional is False


def test_timeline_segmenter_closes_on_a_stable_scene_change() -> None:
    segmenter = TimelineSegmenter(segment_seconds=300, min_scene_seconds=30)
    segmenter.append(_record("r1", 0, scene="editing_code"))
    segmenter.append(_record("r2", 35, scene="editing_code"))

    segments = segmenter.append(_record("r3", 40, scene="watching_video", application="VLC"))

    assert len(segments) == 1
    assert segments[0].scene == "editing_code"
    assert [record.record_id for record in segmenter.pending_records] == ["r3"]


def test_timeline_segmenter_flushes_provisional_tail() -> None:
    segmenter = TimelineSegmenter()
    segmenter.append(_record("r1", 0, summary="当前画面"))

    segment = segmenter.flush()

    assert segment is not None
    assert segment.provisional is True
    assert segment.as_dict()["start_at"].endswith("+00:00")
    assert segmenter.pending_records == ()

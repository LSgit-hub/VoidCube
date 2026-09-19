from __future__ import annotations

from datetime import datetime, timedelta, timezone

from voidcube.systems.perception import TimelineSegment, TimelineStore


def _segment(segment_id: str, offset: int = 0) -> TimelineSegment:
    start = datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(minutes=offset)
    return TimelineSegment(
        segment_id=segment_id,
        start_at=start,
        end_at=start + timedelta(minutes=5),
        scene="editing_code",
        application="VS Code",
        summary="编辑代码",
        key_events=("打开文件",),
        source_record_ids=("record-1",),
        source_count=1,
        confidence=0.8,
        facts=("看到代码",),
        inferences=("可能在调试",),
        unknowns=("是否保存",),
    )


def test_timeline_store_is_idempotent_and_queries_overlapping_range(tmp_path) -> None:
    path = tmp_path / "perception-timeline.sqlite3"
    with TimelineStore(path) as store:
        segment = _segment("segment-1")
        assert store.put(segment) is True
        assert store.put(segment) is False
        assert store.count() == 1
        rows = store.query(
            start_at=datetime(2026, 1, 1, 0, 2, tzinfo=timezone.utc),
            end_at=datetime(2026, 1, 1, 0, 3, tzinfo=timezone.utc),
        )

    assert len(rows) == 1
    assert rows[0].summary == "编辑代码"
    assert rows[0].inferences == ("可能在调试",)


def test_timeline_store_deletes_expired_segments_without_frames(tmp_path) -> None:
    path = tmp_path / "perception-timeline.sqlite3"
    with TimelineStore(path) as store:
        store.put(_segment("old"))
        store.put(_segment("new", offset=100))
        deleted = store.close_expired(
            retention_days=1,
            now=datetime(2026, 1, 2, 0, 10, tzinfo=timezone.utc),
        )
        assert deleted == 1
        assert store.count() == 1
        with store._connect() as connection:
            table_columns = {
                row[1] for row in connection.execute(
                    "PRAGMA table_info(perception_timeline_segments)"
                ).fetchall()
            }
        assert "summary" in table_columns
        assert "frame_bytes" not in table_columns


def test_timeline_store_put_many_is_idempotent(tmp_path) -> None:
    with TimelineStore(tmp_path / "batch.sqlite3") as store:
        assert store.put_many([_segment("a"), _segment("b", offset=10)]) == 2
        assert store.put_many([_segment("a"), _segment("b", offset=10)]) == 0
        assert store.count() == 2

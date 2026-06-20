from datetime import datetime, timezone

from memai import ChroniclePipeline, TranscriptTurn


def test_pipeline_extracts_events_and_scenes() -> None:
    turns = [
        TranscriptTurn(
            turn_id="turn_001",
            speaker="user",
            text="今天我们决定把这个项目做成时间优先的记忆系统。",
            timestamp=datetime(2026, 3, 22, 9, 0, tzinfo=timezone.utc),
        ),
        TranscriptTurn(
            turn_id="turn_002",
            speaker="assistant",
            text="This week we implemented the schema and built the event extractor.",
            timestamp=datetime(2026, 3, 22, 10, 0, tzinfo=timezone.utc),
        ),
        TranscriptTurn(
            turn_id="turn_003",
            speaker="user",
            text="但是今天检索排序还有问题，需要继续修订。",
            timestamp=datetime(2026, 3, 22, 15, 0, tzinfo=timezone.utc),
        ),
    ]

    result = ChroniclePipeline().ingest(turns)

    assert len(result.events) >= 3
    assert len(result.scenes) >= 1
    assert len(result.arcs) >= 1
    assert any("memory-system" in event.topics for event in result.events)
    assert any(scene.child_ids for scene in result.scenes)


def test_query_engine_returns_range_summary() -> None:
    turns = [
        TranscriptTurn(
            turn_id="turn_001",
            speaker="user",
            text="今天我们决定把这个项目做成时间优先的记忆系统。",
            timestamp=datetime(2026, 3, 22, 9, 0, tzinfo=timezone.utc),
        ),
        TranscriptTurn(
            turn_id="turn_002",
            speaker="assistant",
            text="This week we implemented the schema and built the event extractor.",
            timestamp=datetime(2026, 3, 22, 10, 0, tzinfo=timezone.utc),
        ),
        TranscriptTurn(
            turn_id="turn_003",
            speaker="user",
            text="今天我们改成先做查询接口，然后继续修订检索。",
            timestamp=datetime(2026, 3, 23, 9, 0, tzinfo=timezone.utc),
        ),
    ]

    result = ChroniclePipeline().ingest(turns)
    response = result.create_query_engine().range_query(
        start=datetime(2026, 3, 22, 0, 0, tzinfo=timezone.utc),
        end=datetime(2026, 3, 23, 23, 59, tzinfo=timezone.utc),
    )

    assert response["result_type"] == "range_summary"
    assert response["main_arcs"] or response["side_arcs"]
    assert response["confidence"] >= 0.4


def test_pipeline_extracts_profile_memories() -> None:
    turns = [
        TranscriptTurn(
            turn_id="turn_001",
            speaker="user",
            text="Please use Chinese responses and concise summaries.",
            timestamp=datetime(2026, 3, 22, 9, 0, tzinfo=timezone.utc),
        ),
        TranscriptTurn(
            turn_id="turn_002",
            speaker="assistant",
            text="The project must preserve evidence traces.",
            timestamp=datetime(2026, 3, 22, 10, 0, tzinfo=timezone.utc),
        ),
    ]

    result = ChroniclePipeline().ingest(turns)

    assert result.profile_memories
    assert any(
        item.memory_kind.value == "preference" for item in result.profile_memories
    )
    assert any(
        item.memory_kind.value == "constraint" for item in result.profile_memories
    )

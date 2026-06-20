from __future__ import annotations

from datetime import datetime, timezone

from memai import (
    AudioSegmentAdapter,
    ImageCaptionAdapter,
    EventKind,
    MemoryDiffEngine,
    MemoryStateRepository,
    PipelineResult,
    TextTurnAdapter,
    TranscriptTurn,
)


def test_text_turn_adapter_produces_memory_signals() -> None:
    turn = TranscriptTurn(
        turn_id="turn_001",
        speaker="user",
        text="hello",
        timestamp=datetime(2026, 3, 22, 9, 0, tzinfo=timezone.utc),
    )

    signals = TextTurnAdapter().adapt([turn])

    assert len(signals) == 1
    assert signals[0].modality == "text"
    assert signals[0].to_turn().turn_id == "turn_001"


def test_audio_and_image_adapters_produce_memory_signals() -> None:
    timestamp = datetime(2026, 3, 22, 9, 0, tzinfo=timezone.utc)
    audio_signals = AudioSegmentAdapter().adapt(
        [
            {
                "segment_id": "audio_001",
                "timestamp": timestamp,
                "transcript": "I recorded a voice note about the project.",
                "speaker": "user",
                "duration_seconds": 12,
                "confidence": 0.91,
            }
        ]
    )
    image_signals = ImageCaptionAdapter().adapt(
        [
            {
                "image_id": "image_001",
                "timestamp": timestamp,
                "caption": "Whiteboard sketch of the memory timeline.",
                "objects": ["whiteboard", "timeline"],
                "location": "office",
            }
        ]
    )

    assert audio_signals[0].modality == "audio"
    assert audio_signals[0].metadata["duration_seconds"] == 12
    assert image_signals[0].modality == "image"
    assert "timeline" in image_signals[0].metadata["objects"]


def test_memory_diff_engine_reports_added_ids_and_topics() -> None:
    from memai import ChroniclePipeline

    previous = ChroniclePipeline().ingest_dicts(
        [
            {
                "turn_id": "turn_001",
                "speaker": "user",
                "text": "今天我们决定把这个项目做成时间优先的记忆系统。",
                "timestamp": "2026-03-22T09:00:00Z",
            }
        ]
    )
    current = ChroniclePipeline().ingest_dicts(
        [
            {
                "turn_id": "turn_001",
                "speaker": "user",
                "text": "今天我们决定把这个项目做成时间优先的记忆系统。",
                "timestamp": "2026-03-22T09:00:00Z",
            },
            {
                "turn_id": "turn_002",
                "speaker": "assistant",
                "text": "2026-03-23 we refined the revision rules and retrieval plan.",
                "timestamp": "2026-03-23T10:00:00Z",
            },
        ]
    )

    diff = MemoryDiffEngine().compare(previous, current)

    assert diff.added_event_ids
    assert "revision" in diff.new_topics
    assert diff.line_change_records
    assert diff.arc_change_explanations
    assert diff.summary_lines


def test_memory_diff_engine_uses_supersession_lineage_before_topic_signature() -> None:
    from memai import ChroniclePipeline

    previous = ChroniclePipeline().ingest_dicts(
        [
            {
                "turn_id": "turn_001",
                "speaker": "user",
                "text": "2026-03-22 we decided to build the memory system project.",
                "timestamp": "2026-03-22T09:00:00Z",
            }
        ]
    )
    previous_snapshot = MemoryStateRepository()._result_from_dict(previous.to_dict())

    execution = previous.revise_memory(
        target_id=previous_snapshot.events[0].id,
        revision_type="factual_revision",
        reason="The work shifted into retrieval blocking",
        changes={
            "summary": "Retrieval is blocked and needs a revision pass.",
            "topics": ["retrieval"],
            "event_kind": EventKind.BLOCKER,
        },
    )
    current = PipelineResult(
        turns=previous_snapshot.turns,
        events=execution.events,
        scenes=execution.scenes,
        arcs=execution.arcs,
        epochs=execution.epochs,
        profile_memories=previous_snapshot.profile_memories,
        arc_decisions=previous_snapshot.arc_decisions,
        maintenance_engine=previous_snapshot.maintenance_engine,
    )

    diff = MemoryDiffEngine().compare(previous_snapshot, current)

    assert diff.added_arc_ids == []
    assert diff.added_epoch_ids == []
    assert all(
        record["change_type"] != "new_line" for record in diff.line_change_records
    )
    assert all(
        record["change_type"] != "new_chapter" for record in diff.line_change_records
    )
    assert any(
        record["change_type"] == "revised_line" for record in diff.line_change_records
    )
    assert any(
        record["change_type"] == "revised_chapter"
        for record in diff.line_change_records
    )

from datetime import datetime, timezone

from memai import (
    ChroniclePipeline,
    EventKind,
    HeuristicScholarBackend,
    MemoryMaintenanceEngine,
)


def test_maintenance_marks_old_arc_as_dormant_candidate() -> None:
    pipeline = ChroniclePipeline()
    result = pipeline.ingest_dicts(
        [
            {
                "turn_id": "turn_001",
                "speaker": "user",
                "text": "2025-01-03 we decided to build the memory system.",
                "timestamp": "2025-01-03T10:00:00Z",
            },
            {
                "turn_id": "turn_002",
                "speaker": "assistant",
                "text": "2025-01-10 we implemented the schema.",
                "timestamp": "2025-01-10T10:00:00Z",
            },
        ]
    )

    plan = MemoryMaintenanceEngine().plan(
        result.scenes,
        result.arcs,
        reference_time=datetime(2026, 3, 22, 0, 0, tzinfo=timezone.utc),
    )

    assert plan.compression_actions == []
    assert result.arcs[0].id in plan.dormant_arc_ids


def test_default_maintenance_does_not_supersede_tier2_units() -> None:
    pipeline = ChroniclePipeline()
    result = pipeline.ingest_dicts(
        [
            {
                "turn_id": "turn_001",
                "speaker": "user",
                "text": "2025-01-03 we decided to build the memory system.",
                "timestamp": "2025-01-03T10:00:00Z",
            },
            {
                "turn_id": "turn_002",
                "speaker": "assistant",
                "text": "2025-01-10 we implemented the schema and retrieval pipeline.",
                "timestamp": "2025-01-10T10:00:00Z",
            },
        ]
    )

    execution = result.apply_maintenance(
        reference_time=datetime(2026, 3, 22, 0, 0, tzinfo=timezone.utc)
    )

    assert execution.revision_records == []
    assert all(scene.status.value != "superseded" for scene in execution.scenes)
    assert all(arc.status.value != "superseded" for arc in execution.arcs)
    assert any(arc.status.value == "dormant" for arc in execution.arcs)


def test_revise_memory_creates_explicit_supersession() -> None:
    result = ChroniclePipeline().ingest_dicts(
        [
            {
                "turn_id": "turn_001",
                "speaker": "user",
                "text": "今天我们决定把这个项目做成时间优先的记忆系统。",
                "timestamp": "2026-03-22T09:00:00Z",
            }
        ]
    )

    target_id = result.events[0].id
    execution = result.revise_memory(
        target_id=target_id,
        revision_type="factual_revision",
        reason="Need a sharper formulation",
        changes={"summary": "今天我们正式决定将项目定位为时间优先的长期记忆系统。"},
    )

    assert execution.revision_records[0].target_old_id == target_id
    replacements = [
        event for event in execution.events if target_id in event.supersedes
    ]
    assert replacements
    assert replacements[0].summary.startswith("今天我们正式决定")


def test_revision_engine_uses_scholar_backend_draft() -> None:
    class StubScholarBackend(HeuristicScholarBackend):
        def draft_revision(self, unit, *, revision_type, reason, requested_changes):
            payload = super().draft_revision(
                unit,
                revision_type=revision_type,
                reason=reason,
                requested_changes=requested_changes,
            )
            payload["summary"] = "Scholar drafted revision summary"
            return payload

    result = ChroniclePipeline().ingest_dicts(
        [
            {
                "turn_id": "turn_001",
                "speaker": "user",
                "text": "今天我们决定把这个项目做成时间优先的记忆系统。",
                "timestamp": "2026-03-22T09:00:00Z",
            }
        ]
    )

    execution = MemoryMaintenanceEngine(StubScholarBackend()).revise_by_id(
        result.events,
        result.scenes,
        result.arcs,
        result.epochs,
        target_id=result.events[0].id,
        revision_type="factual_revision",
        reason="Need a scholar rewrite",
        changes={},
    )

    replacements = [event for event in execution.events if event.supersedes]
    assert replacements
    assert replacements[0].summary == "Scholar drafted revision summary"


def test_revise_memory_refreshes_parent_chain_after_event_change() -> None:
    result = ChroniclePipeline().ingest_dicts(
        [
            {
                "turn_id": "turn_001",
                "speaker": "user",
                "text": "2026-03-22 we decided to build the memory system project.",
                "timestamp": "2026-03-22T09:00:00Z",
            }
        ]
    )

    old_event_id = result.events[0].id
    old_scene_id = result.scenes[0].id
    old_arc_id = result.arcs[0].id
    old_epoch_id = result.epochs[0].id

    execution = result.revise_memory(
        target_id=old_event_id,
        revision_type="factual_revision",
        reason="The work is blocked on retrieval",
        changes={
            "summary": "Retrieval is blocked and needs a revision pass.",
            "topics": ["retrieval"],
            "event_kind": EventKind.BLOCKER,
        },
    )

    new_event = next(event for event in execution.events if old_event_id in event.supersedes)
    old_event = next(event for event in execution.events if event.id == old_event_id)
    new_scene = next(scene for scene in execution.scenes if old_scene_id in scene.supersedes)
    old_scene = next(scene for scene in execution.scenes if scene.id == old_scene_id)
    new_arc = next(arc for arc in execution.arcs if old_arc_id in arc.supersedes)
    old_arc = next(arc for arc in execution.arcs if arc.id == old_arc_id)
    new_epoch = next(epoch for epoch in execution.epochs if old_epoch_id in epoch.supersedes)
    old_epoch = next(epoch for epoch in execution.epochs if epoch.id == old_epoch_id)

    assert len(execution.revision_records) == 4
    assert old_event.parent_ids == [old_scene_id]
    assert old_scene.child_ids == [old_event_id]
    assert old_scene.parent_ids == [old_arc_id]
    assert old_arc.child_ids == [old_scene_id]
    assert old_arc.parent_ids == [old_epoch_id]
    assert old_epoch.child_ids == [old_arc_id]
    assert "retrieval" in new_scene.topics
    assert new_scene.open_questions == ["Retrieval is blocked and needs a revision pass."]
    assert new_event.parent_ids == [new_scene.id]
    assert new_scene.parent_ids == [new_arc.id]
    assert "retrieval" in new_arc.topics
    assert "Retrieval is blocked and needs a revision pass." in new_arc.obstacles
    assert new_arc.parent_ids == [new_epoch.id]
    assert "retrieval" in new_epoch.epoch_theme

from __future__ import annotations

from datetime import datetime, timezone

from memai import (
    ChroniclePipeline,
    CompressionPolicyDecision,
    CompressionActionSpec,
    HeuristicCompressionPolicy,
)


class StubCompressionPolicy:
    name = "stub"

    def decide(self, scenes, arcs, epochs, reference_time):
        return CompressionPolicyDecision(
            compression_actions=[
                CompressionActionSpec(
                    action_type="compress_scene",
                    source_ids=[scenes[0].id],
                    reason="stub policy compression",
                    target_layer="arc",
                )
            ],
            dormant_arc_ids=[arc.id for arc in arcs[:1]],
            notes=["policy=stub"],
        )


def test_heuristic_compression_policy_emits_notes() -> None:
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

    plan = result.maintenance_engine.plan(
        result.scenes,
        result.arcs,
        result.epochs,
        reference_time=datetime(2026, 3, 22, 0, 0, tzinfo=timezone.utc),
    )

    assert plan.policy_notes
    assert plan.policy_notes[0].startswith("policy=heuristic")


def test_custom_compression_policy_drives_maintenance() -> None:
    pipeline = ChroniclePipeline(compression_policy=StubCompressionPolicy())
    result = pipeline.ingest_dicts(
        [
            {
                "turn_id": "turn_001",
                "speaker": "user",
                "text": "2026-03-20 we decided to build the memory system.",
                "timestamp": "2026-03-20T10:00:00Z",
            },
            {
                "turn_id": "turn_002",
                "speaker": "assistant",
                "text": "2026-03-21 we implemented the schema.",
                "timestamp": "2026-03-21T10:00:00Z",
            },
        ]
    )

    execution = result.apply_maintenance(
        reference_time=datetime(2026, 3, 22, 0, 0, tzinfo=timezone.utc)
    )

    assert execution.plan.policy_notes == ["policy=stub"]
    assert execution.revision_records
    assert any(arc.status.value == "dormant" for arc in execution.arcs)

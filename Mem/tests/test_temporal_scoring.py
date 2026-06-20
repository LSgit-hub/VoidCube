from __future__ import annotations

from memai import (
    ChroniclePipeline,
    TemporalSequencePrediction,
    TransformerTemporalScorerAdapter,
)


class StubTemporalSequenceClient:
    def score_sequence(self, request):
        assert request.scenes
        assert request.scenes[0].scene_id.startswith("scene_")
        return TemporalSequencePrediction(
            frequency=0.9,
            duration=0.8,
            impact=0.9,
            goal_coherence=0.85,
            reactivation=0.7,
            dependency=0.8,
            continuity_bonus=0.15,
            tension_bonus=0.1,
            impact_bonus=0.05,
            total=0.93,
            explanation=["transformer scorer marks this as highly central"],
        )


def test_transformer_temporal_scorer_can_drive_arc_score() -> None:
    pipeline = ChroniclePipeline(
        temporal_scorer=TransformerTemporalScorerAdapter(StubTemporalSequenceClient())
    )
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
                "text": "2026-03-21 we implemented the schema and retrieval pipeline.",
                "timestamp": "2026-03-21T10:00:00Z",
            },
            {
                "turn_id": "turn_003",
                "speaker": "user",
                "text": "2026-03-22 we refined the revision rules.",
                "timestamp": "2026-03-22T10:00:00Z",
            },
        ]
    )

    assert result.arc_decisions
    assert result.arc_decisions[0].classification_score == 0.93
    assert result.arc_decisions[0].classification_reason == [
        "transformer scorer marks this as highly central"
    ]

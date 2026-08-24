from __future__ import annotations

from voidcube.systems.supervisor.endogenous_strategy_projection import (
    build_attention_agenda_projection,
    derive_agenda_persistence_state,
)


def test_agenda_persistence_state_uses_normalized_topic_counters() -> None:
    assert derive_agenda_persistence_state({"seen": 3}) == "persistent"
    assert derive_agenda_persistence_state({"dragging": 2}) == "dragging"
    assert derive_agenda_persistence_state({"resolved": 2, "active_cycles": 2}) == "stabilizing"
    assert derive_agenda_persistence_state({"last_status": "resolved"}) == "cooling"


def test_attention_agenda_projection_is_independent_of_supervisor_state() -> None:
    result = build_attention_agenda_projection(
        deliberation={
            "adaptive_policy": {
                "preferred_focus": "observation",
                "observation_bias": 0.2,
            },
            "reflection": {"dominant_constraint": "weak_learning_yield"},
            "needs": [
                {
                    "need_type": "observe_before_acting",
                    "severity": 0.8,
                    "urgency": 0.7,
                    "confidence": 0.9,
                    "rationale": "evidence is incomplete",
                }
            ],
            "intents": [
                {
                    "intent_type": "request_observation",
                    "source_needs": ["observe_before_acting"],
                    "output_channel": "drive_signal",
                }
            ],
            "signals": [],
        },
        governance_channels={"observation_requests": [{"id": "one"}]},
        strategy_memory={
            "agenda_topic_stats": {
                "observe_before_acting": {"seen": 3, "active_cycles": 3}
            }
        },
    )

    entry = result["entries"][0]
    assert entry["topic"] == "observe_before_acting"
    assert entry["persistence_state"] == "persistent"
    assert entry["observation_required"] is True
    assert result["channel_counts"]["observation_requests"] == 1

from __future__ import annotations

from systems.supervisor.endogenous_strategy_memory import (
    normalize_endogenous_strategy_memory,
)


def test_strategy_memory_normalizer_is_independent_of_runtime_host() -> None:
    result = normalize_endogenous_strategy_memory(
        {
            "agenda_topic_stats": {
                " Observe_Before_Acting ": {
                    "seen": 3,
                    "last_priority": 1.5,
                    "last_status": "ACTIVE",
                }
            },
            "observation_target_stats": {
                "Truthfulness": {"recommended": 2, "last_risk": -0.4}
            },
            "meta_governance_stats": {
                "Observe": {"active_cycles": 1, "last_confidence": 0.8}
            },
        }
    )

    assert result["agenda_topic_stats"]["observe_before_acting"] == {
        "seen": 3,
        "active_cycles": 0,
        "resolved": 0,
        "dragging": 0,
        "last_priority": 1.0,
        "last_confidence": 0.0,
        "last_status": "active",
        "last_seen_at": None,
        "last_resolved_at": None,
        "last_context_key": None,
    }
    assert result["observation_target_stats"]["truthfulness"]["last_risk"] == 0.0
    assert result["meta_governance_stats"]["observe"]["active_cycles"] == 1

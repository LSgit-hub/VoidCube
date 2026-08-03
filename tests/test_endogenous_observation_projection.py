from __future__ import annotations

from systems.supervisor.endogenous_observation_projection import (
    build_observation_program_entries,
    derive_observation_persistence_state,
    project_observation_program,
)


def test_observation_persistence_state_uses_lifecycle_counters() -> None:
    assert derive_observation_persistence_state({"recommended": 3}) == "persistent"
    assert derive_observation_persistence_state({"stalled": 2}) == "stalled"
    assert derive_observation_persistence_state({"last_status": "resolved"}) == "cooling"


def test_observation_projection_separates_entry_building_from_lifecycle_state() -> None:
    entries = build_observation_program_entries(
        uncertainty_ledger={
            "entries": [
                {
                    "domain": "truthfulness",
                    "risk": 0.8,
                    "confidence": 0.7,
                    "recommended_probe": "review corrections",
                    "evidence": ["error_count=2"],
                }
            ]
        },
        governance_channels={
            "observation_requests": [
                {
                    "payload": {"observation_target": "truthfulness"},
                    "signal_type": "truthfulness_review",
                }
            ]
        },
    )
    result = project_observation_program(
        entries,
        target_stats={"truthfulness": {"recommended": 3, "seen": 3}},
    )

    assert result["highest_priority_target"] == "truthfulness"
    assert result["entries"][0]["persistence_state"] == "persistent"
    assert result["entries"][0]["recommended_next_step"] == "collect_observation"
    assert result["entries"][0]["linked_request_signal"] == "truthfulness_review"

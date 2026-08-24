from voidcube.systems.supervisor.endogenous_state_projection import (
    derive_corrective_mode,
    project_drive_history,
    project_governance_event_stream,
)


def test_drive_history_projection_bounds_records_and_uses_explicit_normalizer():
    snapshot = {
        "judgements": [{"id": index} for index in range(25)],
        "outcomes": [{"id": index} for index in range(37)],
        "strategy_memory": {"focus_stats": {"truthfulness": {"count": 1}}},
    }
    normalized = []

    projected = project_drive_history(
        snapshot,
        normalize_strategy_memory=lambda value: normalized.append(value) or {"normalized": True},
    )

    assert len(projected["judgements"]) == 24
    assert len(projected["outcomes"]) == 36
    assert projected["strategy_memory"] == {"normalized": True}
    assert normalized == [snapshot["strategy_memory"]]


def test_governance_event_projection_filters_non_objects_and_bounds_stream():
    projected = project_governance_event_stream(
        {"events": [{"id": index} for index in range(37)] + ["ignored"]}
    )

    assert len(projected["events"]) == 36
    assert projected["events"][0] == {"id": 0}


def test_corrective_mode_prioritizes_corrective_over_guarded_signals():
    assert derive_corrective_mode({})["mode"] == "rest"
    assert derive_corrective_mode({"dynamic_observation_bias_boost": 0.02})["mode"] == "guarded"

    corrective = derive_corrective_mode(
        {
            "dynamic_observation_bias_boost": 0.02,
            "dynamic_truthfulness_bias_boost": 0.02,
            "last_reason": "repair",
        }
    )

    assert corrective["mode"] == "corrective"
    assert corrective["last_reason"] == "repair"
    assert corrective["active_boosts"]["truthfulness_bias"] == 0.02

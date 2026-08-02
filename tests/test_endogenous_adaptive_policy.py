from types import SimpleNamespace

from systems.supervisor.endogenous_adaptive_policy import (
    build_adaptive_policy_projection,
    strategy_context_key,
)


def _projection(*, perception=None, world_model=None, reflection=None, history=None):
    perception_values = {
        "correction_signals": 0,
        "api_a_handoff_count": 0,
        "api_a_running_count": 0,
        "pending_review_count": 0,
        "stale_backlog_count": 0,
        "api_b_judgement_count": 0,
    }
    perception_values.update(perception or {})
    world_values = {
        "truthfulness_pressure": 0.15,
        "memory_pressure": 0.25,
        "body_upgrade_readiness": 0.1,
    }
    world_values.update(world_model or {})
    reflection_values = {
        "learning_yield_state": "mixed",
        "api_b_judgement_blockage_pressure": 0.0,
        "repeated_drive_pressure": 0.0,
        "body_growth_blocked": False,
        "autonomy_readiness": 0.7,
        "recent_learning_quality": 0.5,
        "dominant_constraint": "none",
    }
    reflection_values.update(reflection or {})
    historical_pressure = {
        "scope": "global",
        "total": 0,
        "drag_ratio": 0.0,
        "has_temporal_markers": False,
        "recent_relapse_drag_count": 0,
        "recent_relapse_drag_ratio": 0.0,
    }
    historical_pressure.update(history or {})
    return build_adaptive_policy_projection(
        perception=SimpleNamespace(**perception_values),
        world_model=SimpleNamespace(**world_values),
        reflection=SimpleNamespace(**reflection_values),
        policy={},
        strategy_memory={},
        historical_outcomes=[],
        historical_pressure=historical_pressure,
        context_key="user_chain_quiet|stable|none",
    )


def test_strategy_context_key_normalizes_policy_context_inputs():
    assert strategy_context_key(
        user_mode=" User_Chain_Quiet ",
        system_posture=" Stable ",
        dominant_constraint=" NONE ",
    ) == "user_chain_quiet|stable|none"


def test_adaptive_policy_projection_keeps_neutral_learning_budget():
    projection = _projection()

    assert projection["preferred_focus"] == "memory_continuity"
    assert projection["candidate_budget"] == 4
    assert projection["exploratory_learning_quota"] == 2
    assert projection["body_growth_quota"] == 0


def test_adaptive_policy_projection_forces_truthfulness_focus_on_signal():
    projection = _projection(
        perception={"correction_signals": 3},
        world_model={"truthfulness_pressure": 0.9},
    )

    assert projection["preferred_focus"] == "truthfulness"
    assert projection["truthfulness_bias"] > projection["memory_continuity_bias"]


def test_adaptive_policy_projection_throttles_historical_underdelivery():
    projection = _projection(
        reflection={
            "dominant_constraint": "historical_underdelivery",
            "autonomy_readiness": 0.1,
        },
        history={
            "total": 8,
            "drag_ratio": 0.8,
            "recent_relapse_drag_count": 2,
            "recent_relapse_drag_ratio": 0.67,
        },
    )

    assert projection["preferred_focus"] == "observation"
    assert projection["candidate_budget"] == 1
    assert projection["exploratory_learning_quota"] == 0

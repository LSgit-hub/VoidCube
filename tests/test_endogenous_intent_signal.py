from types import SimpleNamespace

from voidcube.systems.supervisor.endogenous_intent_signal import (
    emit_drive_signal_projections,
    synthesize_intent_projections,
)
from voidcube.systems.supervisor.endogenous_needs import DriveNeed


def _policy(**overrides):
    values = {
        "learning_expansion_bias": 0.5,
        "body_growth_bias": 0.5,
        "governance_hygiene_bias": 0.5,
        "observation_bias": 0.5,
        "truthfulness_bias": 0.5,
        "candidate_throttle": 0.2,
        "candidate_budget": 4,
        "exploratory_learning_quota": 2,
        "body_growth_quota": 0,
        "preferred_focus": "memory_continuity",
        "rationale": "policy rationale",
        "source_evidence": ["unit-test"],
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _perception(**overrides):
    values = {
        "shell_slot_present": True,
        "has_learning_history": False,
        "correction_signals": 0,
        "recent_errors": 0,
        "uncertainty_count": 0,
        "system_posture": "stable",
        "api_b_judgement_count": 0,
        "stale_backlog_count": 0,
        "pending_review_count": 0,
        "learning_quality": 0.0,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _reflection(**overrides):
    values = {
        "api_b_judgement_blockage_pressure": 0.0,
        "dominant_constraint": "none",
        "api_b_judgement_blockage_state": "clear",
        "autonomy_readiness": 0.7,
        "repeated_drive_pressure": 0.0,
        "learning_yield_state": "mixed",
        "rationale": "reflection rationale",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_intent_projection_maps_and_sorts_need_priorities():
    rows = synthesize_intent_projections(
        needs=[
            DriveNeed("observe_before_acting", 0.4, 0.5, 0.8, "observe"),
            DriveNeed("expand_learning_frontier", 0.9, 0.8, 0.9, "learn"),
        ],
        perception=_perception(),
        reflection=_reflection(api_b_judgement_blockage_pressure=0.7),
        adaptive_policy=_policy(learning_expansion_bias=0.9),
    )

    assert rows[0]["intent_type"] == "expand_learning_frontier"
    assert rows[0]["candidate_kind"] == "shell_baseline_learning"
    assert rows[1]["target_horizon"] == "immediate"
    assert rows[1]["output_channel"] == "drive_signal"


def test_signal_projection_covers_governance_truthfulness_observation_and_posture():
    needs = [
        DriveNeed("review_api_b_judgement", 0.6, 0.6, 0.9, "governance"),
        DriveNeed("repair_truthfulness", 0.5, 0.5, 0.9, "truthfulness"),
        DriveNeed("observe_before_acting", 0.7, 0.8, 0.9, "observe"),
    ]
    intents = [
        SimpleNamespace(intent_type="review_governance_hygiene"),
        SimpleNamespace(intent_type="review_truthfulness_signals"),
        SimpleNamespace(intent_type="observe_before_acting"),
    ]
    signals = emit_drive_signal_projections(
        perception=_perception(
            correction_signals=3,
            api_b_judgement_count=1,
            stale_backlog_count=1,
        ),
        world_model=SimpleNamespace(governance_load_state="strained"),
        reflection=_reflection(),
        adaptive_policy=_policy(),
        needs=needs,
        intents=intents,
    )

    signal_types = [signal["signal_type"] for signal in signals]
    assert signal_types.count("governance_review_suggestion") == 1
    assert signal_types.count("observation_signal") == 2
    assert "autonomy_alignment_signal" in signal_types
    assert signal_types[-1] == "drive_posture_signal"
    assert signals[0]["priority"] >= signals[-1]["priority"]


def test_signal_projection_emits_high_learning_observation_without_observe_need():
    signals = emit_drive_signal_projections(
        perception=_perception(learning_quality=80.0),
        world_model=SimpleNamespace(governance_load_state="clear"),
        reflection=_reflection(),
        adaptive_policy=_policy(),
        needs=[],
        intents=[],
    )

    observation = next(signal for signal in signals if signal["signal_type"] == "observation_signal")
    assert observation["payload"]["observation_target"] == "body_growth"
    assert observation["related_intent"] == "prepare_body_growth"

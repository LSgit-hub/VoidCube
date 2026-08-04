from systems.supervisor.config_models import EndogenousDriveCognitiveControlPolicyConfig
from systems.supervisor.endogenous_self_regulation_service import (
    EndogenousSelfRegulationService,
)


def _policy(**overrides):
    policy = EndogenousDriveCognitiveControlPolicyConfig().model_dump(mode="json")
    policy.update(overrides)
    return policy


def test_derives_bounded_regulation_from_explicit_cognitive_snapshots():
    service = EndogenousSelfRegulationService()

    result = service.derive(
        policy=_policy(
            drift_throttle_boost=0.2,
            drift_observation_boost=0.2,
            drift_learning_suppression_boost=0.2,
            weak_channel_count_observe_cap=2,
            weak_channel_observation_step=0.1,
            weak_channel_truthfulness_step=0.1,
        ),
        posture_profile={
            "observation_multiplier": 1.0,
            "throttle_multiplier": 1.0,
            "truthfulness_multiplier": 1.0,
            "learning_suppression_multiplier": 1.0,
        },
        recent_cognitive_alignment={
            "available": True,
            "average_score": 0.2,
            "quality_counts": {"weak": 2, "partial": 0},
        },
        lm_reasoning_state={
            "proposal_drift_memory": {
                "drift_state": "drifting",
                "average_score": 0.2,
            },
            "recent_reference_alignment": {
                "available": True,
                "average_alignment_score": 0.8,
                "weak_or_partial_count": 0,
            },
            "evidence_basis": {
                "self_iteration_readiness_score": 0.8,
                "weak_or_missing_channels": ["learning", "research"],
                "self_understanding_gaps": [],
            },
        },
    )

    assert 0.0 < result["dynamic_candidate_throttle_boost"] <= 1.0
    assert 0.0 < result["dynamic_observation_bias_boost"] <= 1.0
    assert 0.0 < result["dynamic_learning_expansion_suppression"] <= 1.0
    assert "proposal_drift_is_active" in result["last_reason"]


def test_returns_zero_regulation_when_explicit_snapshots_are_clear():
    service = EndogenousSelfRegulationService()

    result = service.derive(
        policy=_policy(),
        posture_profile={},
        recent_cognitive_alignment={
            "available": True,
            "average_score": 0.9,
            "quality_counts": {"strong": 3, "partial": 0, "weak": 0},
        },
        lm_reasoning_state={
            "proposal_drift_memory": {
                "drift_state": "stable",
                "average_score": 0.9,
            },
            "recent_reference_alignment": {
                "available": True,
                "average_alignment_score": 0.9,
                "weak_or_partial_count": 0,
            },
            "evidence_basis": {
                "self_iteration_readiness_score": 0.9,
                "weak_or_missing_channels": [],
                "self_understanding_gaps": [],
            },
        },
    )

    assert result == {
        "dynamic_candidate_throttle_boost": 0.0,
        "dynamic_observation_bias_boost": 0.0,
        "dynamic_truthfulness_bias_boost": 0.0,
        "dynamic_learning_expansion_suppression": 0.0,
        "last_reason": None,
    }

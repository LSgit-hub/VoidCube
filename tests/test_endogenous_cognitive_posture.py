from voidcube.systems.supervisor.config_models import EndogenousDriveCognitiveControlPolicyConfig
from voidcube.systems.supervisor.endogenous_cognitive_posture import (
    resolve_cognitive_posture_from_policy,
)


def _policy(**overrides):
    policy = EndogenousDriveCognitiveControlPolicyConfig().model_dump(mode="json")
    policy.update(overrides)
    return policy


def _resolve(*, policy=None, perception=None, reflection=None, self_model=None,
             evidence=None, alignment=None, drift=None):
    return resolve_cognitive_posture_from_policy(
        policy=policy or _policy(),
        deliberation_dict={
            "perception": perception or {},
            "reflection": reflection or {},
        },
        self_model_snapshot=self_model or {},
        evidence_credibility_summary=evidence or {},
        recent_reference_alignment=alignment or {},
        proposal_drift_memory=drift or {},
        recent_cognitive_alignment={"average_score": 0.8},
    )


def test_manual_profile_wins_over_automatic_pressure():
    result = _resolve(
        policy=_policy(
            posture_selection_mode="manual",
            active_posture_profile="truthfulness_first",
        ),
        perception={"active_sessions": 4, "correction_signals": 8},
    )

    assert result["name"] == "truthfulness_first"
    assert result["selection_reason"] == "manual_selection"
    assert result["selection_mode"] == "manual"


def test_service_pressure_selects_conservative_posture():
    result = _resolve(perception={"active_sessions": 1})

    assert result["name"] == "conservative"
    assert result["selection_reason"] == "service_pressure_requires_conservative_posture"


def test_correction_signals_select_truthfulness_first_posture():
    result = _resolve(perception={"correction_signals": 3})

    assert result["name"] == "truthfulness_first"
    assert result["selection_reason"] == "truthfulness_signals_are_elevated"


def test_drift_and_low_readiness_select_observation_posture():
    drifting = _resolve(
        self_model={"readiness": {"self_iteration_readiness_score": 0.9}},
        drift={"drift_state": "drifting"},
    )
    low_readiness = _resolve(
        self_model={"readiness": {"self_iteration_readiness_score": 0.3}},
    )

    assert drifting["name"] == "observe_first"
    assert low_readiness["name"] == "observe_first"
    assert drifting["selection_reason"] == "drift_or_readiness_requires_observation"


def test_evidence_pressure_selects_evidence_repair_posture():
    result = _resolve(
        evidence={"weak_or_missing_channels": ["learning", "research", "shell"]},
    )

    assert result["name"] == "evidence_repair_first"
    assert result["selection_reason"] == "evidence_repair_pressure_is_elevated"


def test_missing_explanation_memory_selects_observation_posture():
    result = _resolve(
        drift={"missing_posture_alignment_count": 2},
    )

    assert result["name"] == "observe_first"
    assert result["selection_reason"] == "missing_explanation_memory_requires_observation"


def test_inconsistent_explanation_with_missing_memory_selects_evidence_repair():
    result = _resolve(
        drift={
            "missing_priority_basis_count": 2,
            "priority_basis_health": "inconsistent",
        },
    )

    assert result["name"] == "evidence_repair_first"
    assert result["selection_reason"] == "explanation_quality_requires_evidence_repair"


def test_truthfulness_explanation_conflict_selects_truthfulness_posture():
    result = _resolve(
        drift={
            "posture_alignment_health": "inconsistent",
            "dominant_posture_conflict_reason": "truthfulness signal conflict",
        },
    )

    assert result["name"] == "truthfulness_first"
    assert result["selection_reason"] == "explanation_conflict_requires_truthfulness_repair"

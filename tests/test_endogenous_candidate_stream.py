from types import SimpleNamespace

from systems.supervisor.endogenous_candidate_eligibility import (
    CandidateStreamEligibility,
)
from systems.supervisor.endogenous_candidate_stream import build_candidate_stream


def _policy():
    return SimpleNamespace(
        candidate_budget=4,
        exploratory_learning_quota=2,
        body_growth_quota=1,
        preferred_focus="memory_continuity",
        observation_bias=0.2,
        memory_continuity_bias=0.7,
        truthfulness_bias=0.6,
        learning_expansion_bias=0.5,
        governance_hygiene_bias=0.5,
        body_growth_bias=0.5,
        candidate_throttle=0.1,
    )


def _eligibility(**overrides):
    values = {
        "active_candidate_kinds": frozenset(),
        "memory_maintenance": False,
        "truthfulness_review": False,
        "shell_baseline_learning": False,
        "exploratory_learning": False,
        "governance_hygiene_review": False,
        "body_improvement": False,
        "governance_signal_present": False,
    }
    values.update(overrides)
    return CandidateStreamEligibility(**values)


def _kwargs(**overrides):
    values = {
        "drive_input": {},
        "activity": {},
        "drive_context": {},
        "policy": {},
        "shell_slot_meta": {},
        "existing_keys": set(),
        "perception": SimpleNamespace(active_sessions=0),
        "adaptive_policy": _policy(),
        "eligibility": _eligibility(),
        "body_projection": {},
        "lm_candidates": [],
        "cognitive_assessment_memory": {},
        "self_iteration_trend_memory": {},
        "backlog_pressure_penalties": {
            "memory_maintenance": 0.0,
            "self_learning": 0.0,
            "body_improvement": 0.0,
        },
        "memory_maintenance_urgency": 0.8,
        "governance_hygiene_urgency": 0.7,
        "drive_judgements": {
            "memory_maintenance": {},
            "truthfulness_review": {},
            "shell_baseline_learning": {},
            "exploratory_learning": {},
            "governance_hygiene_review": {},
            "body_improvement": {},
        },
    }
    values.update(overrides)
    return values


def test_candidate_stream_owner_returns_empty_when_all_gates_are_closed():
    assert build_candidate_stream(**_kwargs()) == []


def test_candidate_stream_owner_builds_memory_candidate_from_explicit_inputs():
    result = build_candidate_stream(
        **_kwargs(
            drive_input={"checks": {"memory": True}, "idle_seconds": {"memory": 300}},
            drive_context={"api_b_judgement_count": 0},
            eligibility=_eligibility(memory_maintenance=True),
        )
    )

    assert len(result) == 1
    assert result[0].stable_key == "continuity:memory_maintenance_sweep"
    assert result[0].evidence["observation_checks"] == {"memory": True}

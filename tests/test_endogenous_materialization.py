from dataclasses import dataclass

from systems.supervisor.endogenous_materialization import (
    eligible_lm_candidate_kinds,
    has_governance_hygiene_review_signal,
    has_historical_governance_hygiene_review_signal,
    materialize_lm_proposals,
    score_lm_proposal_cognitive_alignment,
)


@dataclass
class Policy:
    memory_continuity_bias: float = 0.6
    truthfulness_bias: float = 0.7
    learning_expansion_bias: float = 0.5
    governance_hygiene_bias: float = 0.55
    body_growth_bias: float = 0.65
    candidate_throttle: float = 0.1
    preferred_focus: str = "truthfulness"


def test_governance_hygiene_signals_are_pure_and_use_explicit_inputs():
    assert has_governance_hygiene_review_signal(
        0,
        0,
        4,
    ) is True
    assert has_governance_hygiene_review_signal(
        0,
        0,
        3,
    ) is False
    assert has_historical_governance_hygiene_review_signal(
        [
            {"task_family": "self_evolution", "status": "deferred"},
            {"governance_task_type": "general_self_evolution", "status": "retry"},
        ]
    ) is True
    assert has_historical_governance_hygiene_review_signal(
        [{"task_family": "self_evolution", "status": "completed"}]
    ) is False


def test_eligibility_keeps_active_and_unsafe_kinds_outside_materialization():
    eligible = eligible_lm_candidate_kinds(
        active_candidate_kinds={"truthfulness_review"},
        self_evolution_eligible=False,
        body_projection_available=False,
        body_growth_quota=0,
        governance_signal_present=False,
    )

    assert "truthfulness_review" not in eligible
    assert "body_improvement" not in eligible
    assert "governance_hygiene_review" not in eligible
    assert "memory_maintenance" in eligible


def test_cognitive_alignment_is_pure_and_preserves_grounding_reasons():
    alignment = score_lm_proposal_cognitive_alignment(
        candidate_kind="exploratory_learning",
        task_type="learning",
        evidence_level="moderate",
        risk_level="medium",
        observation_required=False,
        execution_mode="guarded_execution",
        blocking_factors=[],
        reference_alignment={
            "alignment_score": 0.2,
            "grounding_penalty": 0.4,
            "matched_evidence_nodes": [],
            "matched_agenda_nodes": [],
            "missing_primary_evidence_nodes": ["self_structure"],
            "missing_primary_agenda_nodes": ["focus:learning"],
        },
        evidence_packet={
            "task_type_priors": {
                "top_priority_task_type": "observation",
                "top_priority_score": 0.8,
                "priors": [{"task_type": "learning", "score": 0.4}],
            },
            "evidence_credibility_summary": {
                "weak_or_missing_channels": ["recent_learning"],
                "high_credibility_channels": [],
            },
            "self_model_snapshot": {
                "self_understanding_gaps": ["missing_structure"],
            },
            "cognitive_posture": {"name": "evidence_repair_first"},
        },
        posture_alignment=[],
        priority_basis=[],
    )

    assert alignment["quality"] in {"weak", "partial"}
    assert "proposal_does_not_reference_evidence_graph" in alignment["reasons"]
    assert "proposal_does_not_reference_agenda_graph" in alignment["reasons"]


def test_materialize_lm_proposals_builds_a_scored_candidate_without_engine_state():
    candidates = materialize_lm_proposals(
        proposals=[
            {
                "candidate_kind": "exploratory_learning",
                "title": "Research checkpoint compaction",
                "summary": "Review checkpoint compaction tradeoffs.",
                "confidence": 0.8,
                "referenced_evidence_nodes": ["self_structure"],
                "referenced_agenda_nodes": ["focus:learning"],
            }
        ],
        existing_keys=set(),
        evidence_graph={
            "nodes": [{"topic": "self_structure", "avg_confidence": 0.8}]
        },
        agenda_graph={"focus": "learning", "focus_confidence": 0.8},
        evidence_packet={
            "task_type_priors": {
                "top_priority_task_type": "learning",
                "top_priority_score": 0.8,
                "priors": [{"task_type": "learning", "score": 0.8}],
            },
            "evidence_credibility_summary": {},
            "self_model_snapshot": {},
            "cognitive_posture": {},
        },
        batch_cognitive_assessment={},
        adaptive_policy=Policy(),
        body_projection={"available": False},
        eligible_candidate_kinds={"exploratory_learning"},
        active_sessions=0,
        backlog_pressure=lambda *_: 0.0,
        drive_judgement=lambda kind: {"candidate_kind": kind},
    )

    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate.stable_key.startswith("lm:creativity:exploratory:")
    assert candidate.metadata["drive_judgement"]["candidate_kind"] == (
        "exploratory_learning"
    )
    assert candidate.evidence["active_sessions"] == 0

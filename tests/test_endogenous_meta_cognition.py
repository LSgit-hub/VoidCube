from systems.supervisor.endogenous_meta_cognition import (
    build_meta_cognition_profile,
    build_proposal_drift_memory,
    build_recent_cognitive_alignment_summary,
)


def test_recent_alignment_summary_keeps_only_compact_quality_and_score_projection():
    result = build_recent_cognitive_alignment_summary(
        {
            "outcomes": [
                {"cognitive_alignment": {"score": 0.36, "quality": "weak"}},
                {"metadata": {"cognitive_alignment": {"score": 0.64, "quality": "partial"}}},
            ]
        }
    )

    assert result == {
        "available": True,
        "average_score": 0.5,
        "quality_counts": {"strong": 0, "partial": 1, "weak": 1},
    }


def test_proposal_drift_memory_projects_alignment_and_explanation_health():
    result = build_proposal_drift_memory(
        {
            "drive_history": {
                "outcomes": [
                    {
                        "cognitive_alignment": {
                            "score": 0.34,
                            "quality": "weak",
                            "reasons": ["posture_conflicts_with_observe_first"],
                        },
                        "llm_posture_alignment": ["pushes action before observation"],
                        "llm_priority_basis": ["weak channels still unresolved"],
                    },
                    {
                        "cognitive_alignment": {
                            "score": 0.72,
                            "quality": "strong",
                            "reasons": ["matches_program_top_task_type_prior"],
                        },
                        "llm_posture_alignment": ["follows observe_first"],
                        "llm_priority_basis": ["evidence gaps dominate agenda"],
                    },
                ]
            }
        }
    )

    assert result["average_score"] == 0.53
    assert result["drift_state"] == "correcting"
    assert result["posture_alignment_signal_count"] == 2
    assert result["priority_basis_signal_count"] == 2
    assert result["posture_alignment_health"] == "inconsistent"
    assert result["priority_basis_health"] == "inconsistent"
    assert result["dominant_posture_conflict_reason"] == "posture_conflicts_with_observe_first"


def test_meta_cognition_profile_prioritizes_judgement_and_grounding_pressure():
    result = build_meta_cognition_profile(
        grounding_focus={"grounding_gaps": ["missing source"], "contradictory_topics": []},
        self_iteration_hypotheses={
            "top_target_domain": "grounding",
            "dominant_hypothesis": "repair evidence grounding before expansion",
            "hypotheses": [],
        },
        cognitive_assessment_memory={
            "current_judgement": "review grounding before improvement",
            "dominant_constraint": "weak grounding",
            "self_iteration_target": "grounding",
            "self_iteration_hypothesis": "repair evidence grounding before expansion",
            "why_not_improvement_now": "evidence is not ready",
        },
        self_iteration_trend_memory={"dominant_stay_or_switch": "stay"},
        switch_self_regulation_memory={"stay_effectiveness": "strong"},
        post_task_effect_memory={"effect_direction": "mixed"},
        proposal_drift_memory={"drift_state": "stable"},
        task_type_priors={"top_priority_task_type": "learning"},
    )

    assert result["available"] is True
    assert result["governance_posture"] == "review"
    assert result["grounding_pressure"] == "medium"
    assert result["top_self_iteration_domain"] == "grounding"
    assert result["stay_or_switch_bias"] == "stay"
    assert result["switch_bias_effectiveness"] == "strong"


def test_meta_cognition_profile_is_unavailable_without_signals():
    result = build_meta_cognition_profile(
        grounding_focus={},
        self_iteration_hypotheses={},
        cognitive_assessment_memory={},
        self_iteration_trend_memory={},
        switch_self_regulation_memory={},
        post_task_effect_memory={},
        proposal_drift_memory={},
        task_type_priors={},
    )

    assert result == {
        "available": False,
        "summary": "当前还没有可用的统一元认知画像。",
    }

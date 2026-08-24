from voidcube.systems.supervisor.endogenous_self_iteration import build_self_iteration_hypotheses


def test_self_iteration_hypotheses_are_unavailable_without_signals():
    result = build_self_iteration_hypotheses(
        self_model_snapshot={"readiness": {"self_iteration_readiness_score": 0.9}},
        evidence_credibility_summary={"weak_or_missing_channels": []},
        task_type_priors={"top_priority_task_type": "learning", "top_priority_score": 0.8},
        recent_reference_alignment={"average_alignment_score": 0.9},
        proposal_drift_memory={"drift_state": "stable"},
        cognitive_assessment_memory={},
        self_iteration_trend_memory={},
        switch_self_regulation_memory={},
        post_task_effect_memory={},
        grounding_focus={"grounding_gaps": []},
    )

    assert result == {
        "available": False,
        "hypotheses": [],
        "summary": "No explicit self-iteration hypotheses are available yet.",
    }


def test_self_iteration_hypotheses_prioritize_grounding_and_keep_thin_evidence():
    result = build_self_iteration_hypotheses(
        self_model_snapshot={
            "readiness": {"self_iteration_readiness_score": 0.8},
            "self_understanding_gaps": [],
        },
        evidence_credibility_summary={"weak_or_missing_channels": []},
        task_type_priors={"top_priority_task_type": "review", "top_priority_score": 0.72},
        recent_reference_alignment={"average_alignment_score": 0.41},
        proposal_drift_memory={"drift_state": "stable"},
        cognitive_assessment_memory={"dominant_constraint": "weak_grounding"},
        self_iteration_trend_memory={},
        switch_self_regulation_memory={},
        post_task_effect_memory={},
        grounding_focus={"grounding_gaps": ["missing_evidence:self_structure"]},
    )

    assert result["top_target_domain"] == "grounding"
    assert result["hypotheses"][0]["evidence"] == [
        "missing_evidence:self_structure",
        "dominant_constraint:weak_grounding",
    ]

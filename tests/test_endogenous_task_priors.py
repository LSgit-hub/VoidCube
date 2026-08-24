from voidcube.systems.supervisor.endogenous_task_priors import build_task_type_priors


def _inputs(**overrides):
    values = {
        "reflection": {"dominant_constraint": "unknown"},
        "adaptive_policy": {"preferred_focus": "observation"},
        "self_model_snapshot": {"self_understanding_gaps": []},
        "evidence_credibility_summary": {
            "weak_or_missing_channels": [],
            "high_credibility_channels": [],
        },
        "agenda_graph": {"unresolved_gaps": []},
        "recent_reference_alignment": {"average_alignment_score": 0.8},
        "proposal_drift_memory": {"drift_state": "stable"},
    }
    values.update(overrides)
    return values


def test_task_type_priors_make_observation_the_default_safe_shape():
    result = build_task_type_priors(**_inputs())

    assert result["top_priority_task_type"] == "observation"
    assert result["priors"][0]["reasons"] == ["baseline_program_prior"]


def test_task_type_priors_raise_review_for_truthfulness_and_drift():
    result = build_task_type_priors(
        **_inputs(
            adaptive_policy={"preferred_focus": "truthfulness"},
            recent_reference_alignment={"average_alignment_score": 0.4},
            proposal_drift_memory={
                "available": True,
                "drift_state": "drifting",
                "average_score": 0.3,
            },
        )
    )

    review = next(row for row in result["priors"] if row["task_type"] == "review")
    assert result["top_priority_task_type"] == "review"
    assert "preferred_focus_is_truthfulness" in review["reasons"]
    assert "review_can_help_correct_recent_proposal_drift" in review["reasons"]

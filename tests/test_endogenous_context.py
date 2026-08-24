from voidcube.systems.supervisor.endogenous_context import (
    build_lm_context_layers,
    reference_alignment_gap_labels,
)


def _context_inputs(**overrides):
    values = {
        "cognition_charter": {},
        "cognitive_posture": {
            "name": "evidence_repair_first",
            "selection_reason": "grounding gaps remain",
        },
        "grounding_focus": {
            "primary_evidence_nodes": ["self_structure"],
            "primary_agenda_nodes": ["focus:truthfulness"],
            "grounding_gaps": ["missing_evidence:external_research"],
            "contradictory_topics": ["learning->readiness:contradicts"],
        },
        "self_iteration_hypotheses": {
            "top_target_domain": "grounding",
            "dominant_hypothesis": "observation should precede improvement",
        },
        "meta_cognition_profile": {
            "current_judgement": "observe first",
            "dominant_constraint": "weak evidence",
            "grounding_pressure": "high",
            "governance_posture": "observation",
            "top_self_iteration_domain": "grounding",
            "top_self_iteration_hypothesis": "observe before changing",
        },
        "self_model_snapshot": {
            "self_understanding_gaps": ["missing body trace"],
            "readiness": {"self_iteration_readiness_score": 0.42},
        },
        "evidence_credibility_summary": {
            "weak_or_missing_channels": ["external_research"],
        },
        "task_type_priors": {
            "top_priority_task_type": "observation",
            "top_priority_score": 1.4,
        },
        "cognitive_assessment_memory": {
            "why_not_improvement_now": "evidence is incomplete",
        },
        "self_iteration_trend_memory": {"trend_state": "stable"},
        "switch_self_regulation_memory": {"preferred_switch_bias": "stay"},
        "post_task_effect_memory": {"effect_direction": "helped"},
        "recent_reference_alignment": {"average_alignment_score": 0.65},
        "api_b_judgement_snapshot": {"summary": "one task is pending"},
        "recent_learning_evidence": [
            {"title": "Learning trace", "quality_score": 0.8},
        ],
        "external_research_evidence": [{"title": "Research trace"}],
        "evidence_channels": {
            "channels": [
                {
                    "channel": "recent_learning",
                    "evidence_strength": "strong",
                    "item_count": 1,
                }
            ]
        },
        "recent_learning_titles": ["Learning trace"],
    }
    values.update(overrides)
    return values


def test_default_context_layers_preserve_decision_support_and_long_tail():
    layers = build_lm_context_layers(**_context_inputs())

    assert layers["decision_core"]["current_judgement"] == "observe first"
    assert layers["decision_core"]["secondary_task_shape_score"] == 1.0
    assert layers["decision_core"]["primary_evidence_nodes"] == ["self_structure"]
    assert layers["supporting_detail"]["grounding_gaps"] == [
        "missing_evidence:external_research"
    ]
    assert layers["supporting_detail"]["self_iteration_readiness_score"] == 0.42
    assert layers["long_tail_context"]["recent_learning_evidence"] == [
        {"title": "Learning trace", "quality_score": 0.8}
    ]


def test_charter_layering_policy_selects_only_declared_fields():
    layers = build_lm_context_layers(
        **_context_inputs(
            cognition_charter={
                "context_layering_policy": {
                    "decision_core_fields": ["dominant_constraint", "decision_summary"],
                    "supporting_detail_fields": ["trend_state"],
                    "long_tail_context_fields": ["external_research_titles"],
                }
            }
        )
    )

    assert set(layers["decision_core"]) == {"dominant_constraint", "summary"}
    assert layers["supporting_detail"] == {"trend_state": "stable"}
    assert layers["long_tail_context"] == {
        "external_research_titles": ["Research trace"]
    }


def test_reference_alignment_gap_labels_are_ordered_and_deduplicated():
    labels = reference_alignment_gap_labels(
        {
            "primary_missing_evidence_node": "body_profile",
            "primary_missing_agenda_node": "focus:grounding",
            "recent_entries": [
                {
                    "missing_evidence_nodes": ["body_profile", "research"],
                    "missing_agenda_nodes": ["focus:grounding", "review"],
                },
                {
                    "missing_evidence_nodes": ["later"],
                    "missing_agenda_nodes": [],
                },
            ],
        }
    )

    assert labels == [
        "missing_evidence:body_profile",
        "missing_agenda:focus:grounding",
        "missing_evidence:research",
        "missing_agenda:review",
        "missing_evidence:later",
    ]

from voidcube.systems.supervisor.endogenous_cognitive_posture_context import (
    build_cognitive_posture_context,
)


def test_posture_context_owner_projects_graphs_and_posture_from_explicit_inputs():
    result = build_cognitive_posture_context(
        policy={
            "posture_selection_mode": "manual",
            "active_posture_profile": "balanced",
            "posture_profiles": {"balanced": {"summary": "balanced"}},
        },
        deliberation_dict={
            "perception": {"user_mode": "quiet", "system_posture": "stable"},
            "world_model": {"self_confidence": 0.8},
            "reflection": {"dominant_constraint": "none"},
            "adaptive_policy": {"preferred_focus": "observation"},
            "needs": [],
            "intents": [],
            "signals": [],
        },
        drive_history={},
        recent_learning_evidence=[],
        external_research_evidence=[],
        shell_body_profile={"profile_status": "ready"},
        recent_reference_alignment={"available": False},
        proposal_drift_memory={},
    )

    assert result["cognitive_posture"]["name"] == "balanced"
    assert "evidence_graph" in result
    assert "agenda_graph" in result
    assert result["self_model_snapshot"]["current_state"]["dominant_constraint"] == "none"


def test_posture_context_owner_preserves_reference_and_history_inputs():
    result = build_cognitive_posture_context(
        policy={
            "posture_selection_mode": "auto",
            "posture_profiles": {
                "evidence_repair_first": {"summary": "repair"},
                "balanced": {"summary": "balanced"},
            },
            "auto_evidence_repair_signal_threshold": 1,
        },
        deliberation_dict={
            "perception": {"correction_signals": 0, "active_sessions": 0},
            "world_model": {},
            "reflection": {},
            "adaptive_policy": {},
            "needs": [],
            "intents": [],
        },
        drive_history={
            "outcomes": [
                {
                    "reference_alignment": {
                        "alignment_score": 0.2,
                        "alignment_quality": "weak",
                        "missing_evidence_nodes": ["source-gap"],
                    }
                }
            ]
        },
        recent_learning_evidence=[],
        external_research_evidence=[],
        shell_body_profile={},
        recent_reference_alignment={
            "available": True,
            "weak_or_partial_count": 1,
            "average_alignment_score": 0.2,
            "primary_missing_evidence_node": "source-gap",
        },
        proposal_drift_memory={},
    )

    assert result["cognitive_posture"]["name"] == "evidence_repair_first"
    assert result["self_model_snapshot"]["reference_alignment_feedback"]["available"] is True

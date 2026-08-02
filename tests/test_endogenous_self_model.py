from systems.supervisor.endogenous_self_model import (
    build_evidence_credibility_summary,
    build_recent_reference_alignment,
    build_self_model_snapshot,
)


def test_recent_reference_alignment_projects_compact_feedback():
    result = build_recent_reference_alignment(
        {
            "drive_history": {
                "outcomes": [
                    {
                        "reference_alignment": {
                            "alignment_quality": "partial",
                            "alignment_score": 0.58,
                            "missing_evidence_nodes": ["self_structure"],
                            "missing_agenda_nodes": ["focus:learning_expansion"],
                        }
                    }
                ]
            }
        }
    )

    assert result["available"] is True
    assert result["average_alignment_score"] == 0.58
    assert result["weak_or_partial_count"] == 1
    assert result["primary_missing_evidence_node"] == "self_structure"
    assert result["primary_missing_agenda_node"] == "focus:learning_expansion"


def test_self_model_snapshot_projects_readiness_and_understanding_gaps():
    result = build_self_model_snapshot(
        perception={"user_mode": "idle", "system_posture": "stable"},
        world_model={
            "governance_load_state": "clear",
            "self_confidence": 0.6,
        },
        reflection={
            "learning_yield_state": "weak",
            "dominant_constraint": "none",
            "autonomy_readiness": 0.4,
        },
        adaptive_policy={"preferred_focus": "observation"},
        shell_body_profile={"profile_status": "incomplete"},
        recent_learning_evidence=[],
        external_research_evidence=[],
        recent_reference_alignment={
            "available": True,
            "average_alignment_score": 0.4,
            "weak_or_partial_count": 1,
        },
        evidence_graph={"nodes": [{"topic": "grounding"}]},
        agenda_graph={
            "unresolved_gaps": [{"gap": "missing source"}],
            "recommended_directions": [{"direction": "observe"}],
        },
    )

    assert result["current_topics"] == ["grounding"]
    assert result["unresolved_gaps"] == ["missing source"]
    assert result["current_directions"] == ["observe"]
    assert "body_profile_incomplete" in result["self_understanding_gaps"]
    assert "missing_recent_learning_trace" in result["self_understanding_gaps"]
    assert result["readiness"]["self_iteration_readiness_score"] < 0.52


def test_evidence_credibility_summary_deduplicates_conflict_flags():
    result = build_evidence_credibility_summary(
        recent_learning_evidence=[],
        external_research_evidence=[],
        shell_body_profile={},
        evidence_channels={
            "channels": [
                {"conflict_flags": ["contradiction", "contradiction", "stale"]},
            ]
        },
        recent_reference_alignment={"average_alignment_score": 0.3},
    )

    assert result["weak_or_missing_channels"] == [
        "recent_learning",
        "external_research",
        "shell_body_profile",
    ]
    assert result["conflict_flags"] == ["contradiction", "stale"]
    assert result["reference_alignment_score"] == 0.3

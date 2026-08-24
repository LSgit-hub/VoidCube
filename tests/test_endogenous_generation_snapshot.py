from voidcube.systems.supervisor.endogenous_generation_snapshot import (
    build_lm_task_generation_context_snapshot,
)


def test_generation_snapshot_projects_status_charter_and_evidence_basis():
    snapshot = build_lm_task_generation_context_snapshot(
        evidence_packet={
            "self_model_snapshot": {
                "readiness": {
                    "self_iteration_readiness_score": 1.4,
                    "autonomy_readiness": -0.5,
                },
                "self_understanding_gaps": ["gap-a", "", "gap-b"],
            },
            "evidence_credibility_summary": {
                "high_credibility_channels": ["body"],
                "weak_or_missing_channels": ["research"],
                "reference_alignment_score": 0.62,
            },
            "evidence_channels": {
                "channels": [
                    {
                        "channel": " body ",
                        "kind": "internal",
                        "confidence": 1.7,
                        "evidence_strength": "strong",
                        "item_count": 2,
                    }
                ]
            },
            "proposal_drift_memory": {"drift_state": "stable"},
        },
        cognition_charter={
            "core_mission": "  Ground the next task  ",
            "self_model_principles": [str(index) for index in range(10)],
        },
        role="governance_reasoner",
        max_candidates=-4,
        status="generation_error",
        proposal_count=-2,
        cognitive_assessment={"current_judgement": "observe"},
        error="completion failed",
    )

    assert snapshot["status"] == "generation_error"
    assert snapshot["max_candidates"] == 0
    assert snapshot["proposal_count"] == 0
    assert snapshot["charter"]["core_mission"] == "Ground the next task"
    assert snapshot["charter"]["self_model_principles"] == [
        str(index) for index in range(8)
    ]
    assert snapshot["evidence_basis"]["self_iteration_readiness_score"] == 1.0
    assert snapshot["evidence_basis"]["autonomy_readiness"] == 0.0
    assert snapshot["evidence_basis"]["evidence_channels"][0]["channel"] == "body"
    assert snapshot["evidence_basis"]["evidence_channels"][0]["confidence"] == 1.0
    assert "异常=completion failed" in snapshot["summary"]


def test_generation_snapshot_reads_canonical_and_fallback_thin_memory_fields():
    snapshot = build_lm_task_generation_context_snapshot(
        evidence_packet={
            "self_iteration_hypotheses": {
                "available": True,
                "hypotheses": [
                    {
                        "hypothesis": "fallback hypothesis",
                        "target_domain": "grounding",
                        "priority": 0.75,
                        "suggested_task_types": ["review"],
                    }
                ],
            },
            "self_iteration_trend_memory": {
                "stay_or_switch": "stay",
                "switch_reason": "grounding remains weak",
                "target_signal_count": 3,
                "hypothesis_signal_count": 2,
            },
            "cognitive_assessment_memory": {
                "current_judgement": "review first",
                "target_count": 4,
                "hypothesis_count": 3,
            },
        },
        cognition_charter={},
        role="governance_reasoner",
        max_candidates=2,
        status="completed",
        proposal_count=1,
    )

    hypotheses = snapshot["self_iteration_hypotheses"]
    assert hypotheses["dominant_hypothesis"] == "fallback hypothesis"
    assert hypotheses["top_target_domain"] == "grounding"
    assert hypotheses["hypothesis_count"] == 1
    assert hypotheses["suggested_task_types"] == ["review"]

    trend = snapshot["self_iteration_trend_memory"]
    assert trend["dominant_stay_or_switch"] == "stay"
    assert trend["dominant_switch_reason"] == "grounding remains weak"
    assert trend["target_count"] == 3
    assert trend["hypothesis_count"] == 2

    assessment = snapshot["cognitive_assessment_memory"]
    assert assessment["current_judgement"] == "review first"
    assert assessment["self_iteration_target_count"] == 4
    assert assessment["self_iteration_hypothesis_count"] == 3


def test_generation_snapshot_normalizes_drift_reference_and_posture():
    snapshot = build_lm_task_generation_context_snapshot(
        evidence_packet={
            "proposal_drift_memory": {
                "available": True,
                "average_score": 0.4,
                "posture_alignment_signal_count": "bad",
                "missing_priority_basis_count": -3,
            },
            "recent_reference_alignment": {
                "available": True,
                "average_alignment_score": 0.7,
                "entry_count": "2",
                "primary_missing_evidence_node": " evidence ",
            },
            "cognitive_posture": {
                "name": "review_first",
                "observation_multiplier": 0.8,
                "learning_suppression_multiplier": 1.8,
            },
            "post_task_effect_memory": {
                "available": True,
                "average_quality_score": 0.9,
                "dominant_target_effect": "grounding:helped",
            },
        },
        cognition_charter={},
        role="governance_reasoner",
        max_candidates=1,
        status="completed",
        proposal_count=0,
    )

    assert snapshot["proposal_drift_memory"]["posture_alignment_signal_count"] == 0
    assert snapshot["proposal_drift_memory"]["missing_priority_basis_count"] == 0
    assert snapshot["recent_reference_alignment"]["entry_count"] == 2
    assert snapshot["recent_reference_alignment"]["primary_missing_evidence_node"] == (
        "evidence"
    )
    assert snapshot["cognitive_posture"]["learning_suppression_multiplier"] == 1.0
    assert snapshot["post_task_effect_memory"]["dominant_target_effect"] == (
        "grounding:helped"
    )

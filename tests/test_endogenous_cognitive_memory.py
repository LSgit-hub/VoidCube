from systems.supervisor.endogenous_cognitive_memory import (
    build_cognitive_assessment_memory,
    build_post_task_effect_memory,
    build_self_iteration_trend_memory,
    build_switch_self_regulation_memory,
)


def _context(outcomes):
    return {"drive_history": {"outcomes": outcomes}}


def test_cognitive_assessment_memory_projects_dominant_lm_fields():
    result = build_cognitive_assessment_memory(
        _context(
            [
                {
                    "llm_cognitive_assessment": {
                        "current_judgement": "review grounding",
                        "dominant_constraint": "weak grounding",
                        "why_not_improvement_now": ["evidence is incomplete"],
                        "self_iteration_target": "grounding",
                        "self_iteration_hypothesis": "repair evidence first",
                        "primary_grounding_gaps": ["missing source"],
                    }
                }
            ]
        )
    )

    assert result["available"] is True
    assert result["current_judgement"] == "review grounding"
    assert result["dominant_constraint"] == "weak grounding"
    assert result["self_iteration_target_count"] == 1
    assert result["grounding_gap_count"] == 1
    assert result["entry_count"] == 1


def test_self_iteration_trend_memory_projects_stable_target_and_switch_reason():
    result = build_self_iteration_trend_memory(
        _context(
            [
                {
                    "llm_cognitive_assessment": {
                        "self_iteration_target": "grounding",
                        "self_iteration_hypothesis": "repair evidence first",
                        "stay_or_switch": "switch",
                        "switch_reason": "old target stopped improving",
                    }
                }
            ]
        )
    )

    assert result["dominant_target"] == "grounding"
    assert result["dominant_hypothesis"] == "repair evidence first"
    assert result["dominant_stay_or_switch"] == "switch"
    assert result["dominant_switch_reason"] == "old target stopped improving"
    assert result["target_stability"] == "stable"


def test_switch_self_regulation_memory_compares_switch_and_stay_quality():
    result = build_switch_self_regulation_memory(
        _context(
            [
                {
                    "quality_score": 0.85,
                    "cognitive_alignment": {"score": 0.8},
                    "reference_alignment": {"alignment_score": 0.8},
                    "result_status": "completed",
                    "llm_cognitive_assessment": {"stay_or_switch": "switch"},
                },
                {
                    "quality_score": 0.4,
                    "cognitive_alignment": {"score": 0.45},
                    "reference_alignment": {"alignment_score": 0.5},
                    "result_status": "deferred",
                    "llm_cognitive_assessment": {"stay_or_switch": "stay"},
                },
            ]
        )
    )

    assert result["available"] is True
    assert result["preferred_switch_bias"] == "switch"
    assert result["switch_effectiveness"] == "strong"
    assert result["stay_effectiveness"] == "weak"
    assert result["switch_result_statuses"] == ["completed"]
    assert result["stay_result_statuses"] == ["deferred"]


def test_post_task_effect_memory_ignores_planned_and_classifies_completed_effect():
    result = build_post_task_effect_memory(
        _context(
            [
                {
                    "event_type": "planned",
                    "quality_score": 0.2,
                    "cognitive_alignment": {"score": 0.2},
                    "reference_alignment": {"alignment_score": 0.2},
                    "llm_cognitive_assessment": {"self_iteration_target": "grounding"},
                },
                {
                    "event_type": "decision",
                    "quality_score": 0.8,
                    "cognitive_alignment": {"score": 0.7},
                    "reference_alignment": {"alignment_score": 0.72},
                    "llm_cognitive_assessment": {"self_iteration_target": "grounding"},
                },
            ]
        )
    )

    assert result["available"] is True
    assert result["effect_direction"] == "improving"
    assert result["dominant_target_effect"] == "grounding:helped"
    assert result["entry_count"] == 1

from systems.supervisor.config_models import EndogenousDriveCognitiveControlPolicyConfig
from systems.supervisor.endogenous_cognitive_posture_service import (
    EndogenousCognitivePostureService,
)


def test_runtime_posture_service_reads_current_policy_and_selects_profile():
    policy_model = EndogenousDriveCognitiveControlPolicyConfig(
        posture_selection_mode="manual",
        active_posture_profile="truthfulness_first",
    )
    service = EndogenousCognitivePostureService(
        runtime_config=type(
            "RuntimeConfig",
            (),
            {
                "endogenous_drive_cognition_charter": type(
                    "Charter",
                    (),
                    {"cognitive_control_policy": policy_model},
                )(),
            },
        )(),
    )

    policy = service.current_policy()
    profile = service.active_profile(
        lm_reasoning_state={},
        history_snapshot={"outcomes": []},
        deliberation={},
    )

    assert policy["active_posture_profile"] == "truthfulness_first"
    assert profile["name"] == "truthfulness_first"
    assert profile["selection_mode"] == "manual"
    assert profile["selection_reason"] == "manual_selection"


def test_runtime_posture_service_projects_recent_alignment_for_regulation():
    service = EndogenousCognitivePostureService(runtime_config=None)

    result = service.recent_alignment(
        history_snapshot={
            "outcomes": [
                {
                    "cognitive_alignment": {
                        "score": 0.4,
                        "quality": "weak",
                        "top_priority_task_type": "review",
                    },
                    "llm_posture_alignment": ["observe first"],
                    "llm_priority_basis": ["weak evidence"],
                }
            ]
        }
    )

    assert result["available"] is True
    assert result["average_score"] == 0.4
    assert result["quality_counts"] == {"strong": 0, "partial": 0, "weak": 1}
    assert result["dominant_task_shape"] == "review"
    assert result["posture_alignment_signal_count"] == 1
    assert result["priority_basis_signal_count"] == 1

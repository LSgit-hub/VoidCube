from memai.llm_client import get_memory_context_max_chars
from systems.supervisor.config_models import EndogenousDriveCognitionCharterConfig
from systems.supervisor.endogenous_cognition_charter import resolve_cognition_charter


def test_charter_resolution_fills_runtime_fallbacks_and_prompt_defaults():
    result = resolve_cognition_charter(
        charter_model={},
        core_mission="fallback mission",
        task_generation_principles=["fallback principle"],
    )

    assert result["core_mission"] == "fallback mission"
    assert result["task_generation_policy"] == ["fallback principle"]
    assert result["task_generation_focus"]
    assert result["prompt_output_requirements"]
    assert result["context_layering_policy"]["decision_core_fields"]
    assert result["prompt_attention_policy"]["max_chars"] == get_memory_context_max_chars()
    assert result["prompt_attention_policy"]["structure_keys"]


def test_charter_resolution_preserves_explicit_nested_policy_values():
    result = resolve_cognition_charter(
        charter_model={
            "core_mission": "configured mission",
            "task_generation_policy": ["configured principle"],
            "task_generation_focus": ["configured focus"],
            "prompt_output_requirements": ["configured output"],
            "context_layering_policy": {
                "decision_core_fields": ["configured decision"],
                "supporting_detail_fields": ["configured detail"],
                "long_tail_context_fields": ["configured tail"],
            },
            "prompt_attention_policy": {
                "max_chars": 7000,
                "priority_order": ["configured priority"],
                "structure_keys": ["configured structure"],
                "trim_stage_order": ["configured trim"],
            },
        },
        core_mission="fallback mission",
        task_generation_principles=["fallback principle"],
    )

    assert result["core_mission"] == "configured mission"
    assert result["task_generation_policy"] == ["configured principle"]
    assert result["task_generation_focus"] == ["configured focus"]
    assert result["prompt_output_requirements"] == ["configured output"]
    assert result["context_layering_policy"] == {
        "decision_core_fields": ["configured decision"],
        "supporting_detail_fields": ["configured detail"],
        "long_tail_context_fields": ["configured tail"],
    }
    assert result["prompt_attention_policy"] == {
        "max_chars": 7000,
        "priority_order": ["configured priority"],
        "structure_keys": ["configured structure"],
        "trim_stage_order": ["configured trim"],
    }


def test_charter_resolution_accepts_pydantic_charter_model():
    charter = EndogenousDriveCognitionCharterConfig()

    result = resolve_cognition_charter(charter_model=charter)

    assert result["core_mission"] == charter.core_mission
    assert result["context_layering_policy"]["decision_core_fields"]
    assert result["prompt_attention_policy"]["trim_stage_order"]

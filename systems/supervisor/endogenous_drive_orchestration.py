"""Application orchestration for one endogenous drive evaluation cycle."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Awaitable, Callable, Dict, List, Optional

from systems.supervisor.endogenous_candidate_pipeline import CORE_VALUES
from systems.supervisor.endogenous_state_projection import (
    project_drive_history,
    project_governance_event_stream,
)


JsonDict = Dict[str, Any]
Candidate = Any


@dataclass(frozen=True, slots=True)
class EndogenousDriveEvaluationContext:
    """Explicit runtime services required by the evaluation application flow."""

    runtime_config: Any
    resolve_drive_input_request: Callable[[JsonDict], Awaitable[JsonDict]]
    load_self_regulation: Callable[[], JsonDict]
    load_drive_history: Callable[[], JsonDict]
    normalize_strategy_memory: Callable[[Any], JsonDict]
    api_b_judgement_task_summaries: Callable[[int], List[JsonDict]]
    api_a_execution_lane_task_summaries: Callable[[int], List[JsonDict]]
    build_deliberation_report: Callable[..., Any]
    generate_candidates: Callable[..., List[Candidate]]
    existing_drive_keys: Callable[[], set[str]]
    schedule_candidate_items: Callable[[List[Candidate]], List[JsonDict]]
    lm_generation_application_state: Callable[[], Any]
    derive_cognitive_self_regulation: Callable[..., JsonDict]
    release_cleared_observation_carryover: Callable[..., JsonDict]
    governance_channels_from_deliberation: Callable[[JsonDict], JsonDict]
    persist_evaluation: Callable[..., JsonDict]
    load_governance_events: Callable[[], JsonDict]
    build_cognition_state: Callable[..., JsonDict]
    record_ui_activity: Callable[..., None]
    build_response_fields: Callable[..., Dict[str, JsonDict]]
    drive_posture_from_deliberation: Callable[[JsonDict], JsonDict]
    core_values: Any


def build_endogenous_drive_policy(runtime_config: Any) -> JsonDict:
    """Project runtime tuning into the explicit policy consumed by the Engine."""

    def setting(name: str, default: Any) -> Any:
        return getattr(runtime_config, name, default) or default

    return {
        "learning_topic_cooldown_hours": int(
            setting("endogenous_drive_learning_topic_cooldown_hours", 24)
        ),
        "body_improvement_cooldown_hours": int(
            setting("endogenous_drive_body_improvement_cooldown_hours", 12)
        ),
        "topic_overlap_threshold": float(
            setting("endogenous_drive_topic_overlap_threshold", 0.6)
        ),
        "body_improvement_min_quality": float(
            setting("body_improvement_min_quality", 60.0)
        ),
        "body_improvement_editable_dirs": list(
            setting(
                "body_improvement_editable_dirs",
                ["skills/", "tools/", "agent/", "prompts/"],
            )
        ),
        "body_improvement_forbidden_patterns": list(
            setting(
                "body_improvement_forbidden_patterns",
                ["**/credential*", "**/.env*", "systems/**"],
            )
        ),
        "body_improvement_max_files": int(
            setting("body_improvement_max_files", 5)
        ),
    }


def _merge_self_regulation(
    persisted: JsonDict,
    current: JsonDict,
) -> JsonDict:
    merged = dict(persisted)
    boost_keys = (
        "dynamic_candidate_throttle_boost",
        "dynamic_observation_bias_boost",
        "dynamic_truthfulness_bias_boost",
        "dynamic_learning_expansion_suppression",
    )
    for key in boost_keys:
        merged[key] = round(
            min(
                1.0,
                float(persisted.get(key) or 0.0)
                + float(current.get(key) or 0.0),
            ),
            4,
        )
    merged["last_reason"] = "; ".join(
        item
        for item in (
            str(persisted.get("last_reason") or "").strip(),
            str(current.get("last_reason") or "").strip(),
        )
        if item
    ) or None
    return merged


def _apply_regulation_to_policy(policy: JsonDict, regulation: JsonDict) -> None:
    for key in (
        "dynamic_candidate_throttle_boost",
        "dynamic_observation_bias_boost",
        "dynamic_truthfulness_bias_boost",
        "dynamic_learning_expansion_suppression",
    ):
        policy[key] = float(regulation.get(key) or 0.0)


async def evaluate_endogenous_drive(
    *,
    request: Optional[JsonDict],
    context: EndogenousDriveEvaluationContext,
) -> JsonDict:
    """Run one evaluation while keeping runtime resources behind callbacks."""

    request = dict(request or {})
    record_activity = bool(request.get("record_activity", True))
    persist_evaluation = bool(request.get("persist_evaluation", True))
    drive_input = await context.resolve_drive_input_request(request)
    persisted_self_regulation = dict(context.load_self_regulation() or {})
    api_b_judgement_tasks = context.api_b_judgement_task_summaries(24)
    api_a_execution_lane_tasks = context.api_a_execution_lane_task_summaries(24)
    drive_input["api_b_judgement_tasks"] = api_b_judgement_tasks
    drive_input["api_a_execution_lane_tasks"] = api_a_execution_lane_tasks
    drive_input["autonomous_chain_live_tasks"] = [
        *api_b_judgement_tasks,
        *api_a_execution_lane_tasks,
    ]
    drive_input["endogenous_drive_policy"] = build_endogenous_drive_policy(
        context.runtime_config
    )
    drive_input["drive_history"] = project_drive_history(
        context.load_drive_history(),
        normalize_strategy_memory=context.normalize_strategy_memory,
    )
    self_regulation = dict(persisted_self_regulation)
    _apply_regulation_to_policy(
        drive_input["endogenous_drive_policy"],
        self_regulation,
    )
    max_candidates = int(
        request.get(
            "max_candidates",
            getattr(context.runtime_config, "endogenous_drive_max_candidates", 0),
        )
    )

    deliberation = context.build_deliberation_report(drive_input=drive_input)
    deliberation_dict = deliberation.to_dict()
    candidates = context.generate_candidates(
        drive_input=drive_input,
        existing_drive_keys=context.existing_drive_keys(),
        max_candidates=max_candidates,
        deliberation_report=deliberation,
    )
    candidate_items = context.schedule_candidate_items(candidates)
    lm_application_state = context.lm_generation_application_state()
    lm_reasoning_state = dict(lm_application_state.reasoning_state or {})
    cognitive_self_regulation = context.derive_cognitive_self_regulation(
        drive_history=drive_input["drive_history"],
        lm_reasoning_state=lm_reasoning_state,
        deliberation=deliberation_dict,
    )
    cognitive_self_regulation = context.release_cleared_observation_carryover(
        persisted_self_regulation=self_regulation,
        cognitive_self_regulation=cognitive_self_regulation,
        deliberation=deliberation_dict,
        lm_reasoning_state=lm_reasoning_state,
        drive_history=drive_input["drive_history"],
    )
    combined_self_regulation = _merge_self_regulation(
        self_regulation,
        cognitive_self_regulation,
    )
    _apply_regulation_to_policy(
        drive_input["endogenous_drive_policy"],
        combined_self_regulation,
    )
    boost_keys = (
        "dynamic_candidate_throttle_boost",
        "dynamic_observation_bias_boost",
        "dynamic_truthfulness_bias_boost",
        "dynamic_learning_expansion_suppression",
    )
    if any(float(cognitive_self_regulation.get(key) or 0.0) > 0.0 for key in boost_keys):
        deliberation = context.build_deliberation_report(drive_input=drive_input)
        deliberation_dict = deliberation.to_dict()
        candidates = context.generate_candidates(
            drive_input=drive_input,
            existing_drive_keys=context.existing_drive_keys(),
            max_candidates=max_candidates,
            deliberation_report=deliberation,
            lm_proposals_override=lm_application_state.candidate_repass_proposals,
        )
        candidate_items = context.schedule_candidate_items(candidates)

    governance_channels = context.governance_channels_from_deliberation(
        deliberation_dict
    )
    if persist_evaluation:
        persisted_evaluation = context.persist_evaluation(
            deliberation=deliberation_dict,
            drive_input=drive_input,
            governance_channels=governance_channels,
            self_regulation=combined_self_regulation,
            candidate_items=candidate_items,
            lm_reasoning_state=lm_reasoning_state,
        )
        candidate_items = list(persisted_evaluation["candidate_items"])
        governance_event_stream = dict(
            persisted_evaluation["governance_event_stream"]
        )
        cognition_state = dict(persisted_evaluation["cognition_state"])
    else:
        governance_event_stream = project_governance_event_stream(
            context.load_governance_events()
        )
        cognition_state = context.build_cognition_state(
            deliberation=deliberation_dict,
            governance_channels=governance_channels,
            governance_event_stream=governance_event_stream,
            self_regulation=combined_self_regulation,
            candidate_items=candidate_items,
            lm_reasoning_state=lm_reasoning_state,
        )
    if record_activity:
        context.record_ui_activity(
            "endogenous_drive_evaluated",
            scene="planning",
            summary=f"内生驱动已完成一轮认知评估，并形成了 {len(candidates)} 个候选判断投影。",
            metadata={
                "count": len(candidates),
                "candidate_keys": [candidate.stable_key for candidate in candidates],
                "candidates": [dict(item) for item in candidate_items],
                "deliberation": deliberation_dict,
                "cognition_state": cognition_state,
            },
        )
    response_fields = context.build_response_fields(drive_input=drive_input)
    return {
        "status": "evaluated",
        "enabled": bool(
            getattr(context.runtime_config, "endogenous_drive_enabled", False)
        ),
        "core_values": context.core_values,
        **response_fields,
        "deliberation": deliberation_dict,
        "candidates": candidate_items,
        "count": len(candidates),
        "drive_posture": context.drive_posture_from_deliberation(deliberation_dict),
        "governance_channels": governance_channels,
        "governance_event_stream": governance_event_stream,
        "self_regulation": combined_self_regulation,
        "cognitive_self_regulation": cognitive_self_regulation,
        "cognition_state": cognition_state,
    }

"""Pure deliberation pipeline assembly for endogenous drive."""

from __future__ import annotations

from typing import Any, Dict

from .endogenous_adaptive_policy import build_adaptive_policy
from .endogenous_drive_context import (
    build_drive_context,
    get_shell_slot_meta,
)
from .endogenous_drive_models import (
    DriveAdaptivePolicy,
    DriveDeliberationReport,
    DriveIntent,
    DrivePerceptionSnapshot,
    DriveReflection,
    DriveSignal,
    DriveWorldModel,
)
from .endogenous_drive_state import (
    build_drive_perception_projection,
    build_drive_world_model_projection,
)
from .endogenous_intent_signal import (
    emit_drive_signal_projections,
    synthesize_intent_projections,
)
from .endogenous_materialization import (
    resolve_candidate_eligibility_plan,
)
from .endogenous_needs import detect_needs
from .endogenous_policy import (
    HISTORICAL_OBSERVATION_CARRYOVER_RELEASED,
)
from .endogenous_reflection import build_reflection_projection


_REVIEW_API_B_JUDGEMENT_NEED = "review_api_b_judgement"


def build_deliberation_report(
    *,
    drive_input: Dict[str, Any],
) -> DriveDeliberationReport:
    activity = dict(drive_input.get("activity") or {})
    drive_context = build_drive_context(drive_input)
    nested_counts = dict(activity.get("counts") or {})
    counts: Dict[str, Any] = dict(nested_counts)
    for key in (
        "error_count",
        "recent_errors",
        "uncertainty_high_count",
        "high_uncertainty",
    ):
        value = activity.get(key)
        if value is not None and key not in counts:
            counts[key] = value

    decisions_by_family = dict(drive_input.get("task_family_decisions") or {})
    decisions_by_governance = dict(
        drive_input.get("governance_task_type_decisions") or {}
    )
    memory_plan = resolve_candidate_eligibility_plan(
        "memory_maintenance",
        decisions_by_family,
        decisions_by_governance,
    )
    self_learning_plan = resolve_candidate_eligibility_plan(
        "self_learning",
        decisions_by_family,
        decisions_by_governance,
    )
    autonomous_improvement_plan = resolve_candidate_eligibility_plan(
        "general_self_evolution",
        decisions_by_family,
        decisions_by_governance,
    )
    recent_errors = int(
        counts.get("error_count") or counts.get("recent_errors") or 0
    )
    uncertainty_count = int(
        counts.get("uncertainty_high_count")
        or counts.get("high_uncertainty")
        or 0
    )
    pre_decayed = drive_input.get("correction_signals")
    if pre_decayed is not None:
        try:
            correction_signals = max(0, int(pre_decayed))
        except (TypeError, ValueError):
            correction_signals = recent_errors + uncertainty_count
    else:
        correction_signals = recent_errors + uncertainty_count

    shell_slot_meta = get_shell_slot_meta(drive_input)
    perception = DrivePerceptionSnapshot(
        **build_drive_perception_projection(
            drive_input=drive_input,
            activity=activity,
            drive_context=drive_context,
            counts=counts,
            correction_signals=correction_signals,
            shell_slot_meta=shell_slot_meta,
        )
    )
    world_model = DriveWorldModel(
        **build_drive_world_model_projection(perception)
    )
    reflection = DriveReflection(
        **build_reflection_projection(
            perception=perception,
            world_model=world_model,
            drive_context=drive_context,
            shell_slot_meta=shell_slot_meta,
        )
    )
    adaptive_policy = DriveAdaptivePolicy(
        **build_adaptive_policy(
            perception=perception,
            world_model=world_model,
            reflection=reflection,
            drive_context=drive_context,
        )
    )
    needs = detect_needs(
        perception=perception,
        world_model=world_model,
        reflection=reflection,
        adaptive_policy=adaptive_policy,
        memory_plan=memory_plan,
        self_learning_plan=self_learning_plan,
        autonomous_improvement_plan=autonomous_improvement_plan,
        governance_review_need_type=_REVIEW_API_B_JUDGEMENT_NEED,
        historical_observation_carryover_released=bool(
            drive_context["policy"].get(
                HISTORICAL_OBSERVATION_CARRYOVER_RELEASED,
                False,
            )
        ),
        foundation_projection=dict(drive_input.get("evolution_foundation") or {}),
    )
    intents = [
        DriveIntent(**projection)
        for projection in synthesize_intent_projections(
            needs=needs,
            perception=perception,
            reflection=reflection,
            adaptive_policy=adaptive_policy,
        )
    ]
    signals = [
        DriveSignal(**projection)
        for projection in emit_drive_signal_projections(
            perception=perception,
            world_model=world_model,
            reflection=reflection,
            adaptive_policy=adaptive_policy,
            needs=needs,
            intents=intents,
            foundation_projection=dict(drive_input.get("evolution_foundation") or {}),
        )
    ]
    return DriveDeliberationReport(
        perception=perception,
        world_model=world_model,
        reflection=reflection,
        adaptive_policy=adaptive_policy,
        needs=needs,
        intents=intents,
        signals=signals,
    )

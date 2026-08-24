from voidcube.systems.supervisor.endogenous_drive_judgement import (
    build_drive_judgement_metadata,
    build_intent_metadata,
)
from voidcube.systems.supervisor.endogenous_drive_models import (
    DriveAdaptivePolicy,
    DriveIntent,
    DrivePerceptionSnapshot,
    DriveReflection,
    DriveWorldModel,
)
from voidcube.systems.supervisor.endogenous_needs import DriveNeed


def _inputs():
    perception = DrivePerceptionSnapshot(
        user_mode="user_chain_quiet",
        autonomous_chain_gate_active=False,
        system_posture="stable",
        active_sessions=0,
        recent_errors=0,
        uncertainty_count=0,
        correction_signals=0,
        learning_quality=80.0,
        has_learning_history=True,
        shell_slot_present=True,
        shell_slot_id="slot-B",
        api_b_judgement_count=0,
        learning_backlog_count=0,
        body_improvement_backlog_count=0,
        stale_backlog_count=0,
        pending_review_count=0,
    )
    world_model = DriveWorldModel(
        user_mode="user_chain_quiet",
        system_posture="stable",
        truthfulness_pressure=0.15,
        learning_momentum=0.7,
        body_upgrade_readiness=0.7,
        governance_load_state="clear",
        memory_pressure=0.25,
        self_confidence=0.8,
    )
    reflection = DriveReflection(
        recent_learning_count=1,
        recent_learning_quality=0.8,
        learning_yield_state="strong",
        api_b_judgement_blockage_pressure=0.0,
        api_b_judgement_blockage_state="clear",
        body_growth_blocked=False,
        repeated_drive_pressure=0.0,
        autonomy_readiness=0.8,
        dominant_constraint="none",
        rationale="ready.",
    )
    adaptive_policy = DriveAdaptivePolicy(
        learning_expansion_bias=0.7,
        truthfulness_bias=0.5,
        memory_continuity_bias=0.5,
        governance_hygiene_bias=0.5,
        body_growth_bias=0.6,
        observation_bias=0.4,
        candidate_throttle=0.0,
        candidate_budget=4,
        exploratory_learning_quota=2,
        body_growth_quota=1,
        preferred_focus="learning_expansion",
        rationale="ready.",
    )
    need = DriveNeed(
        need_type="expand_learning_frontier",
        severity=0.7,
        urgency=0.6,
        confidence=0.8,
        rationale="learn.",
    )
    intent = DriveIntent(
        intent_type="expand_learning",
        priority=0.7,
        rationale="learn.",
        target_horizon="next_cycle",
        output_channel="api_b",
        source_needs=[need.need_type],
        candidate_kind="exploratory_learning",
    )
    return perception, world_model, reflection, adaptive_policy, [need], [intent]


def test_drive_judgement_owner_links_intent_needs_and_projection_layers():
    perception, world_model, reflection, policy, needs, intents = _inputs()

    result = build_intent_metadata(
        intent=intents[0],
        needs=needs,
        perception=perception,
        world_model=world_model,
        reflection=reflection,
        adaptive_policy=policy,
    )

    assert result["intent"]["candidate_kind"] == "exploratory_learning"
    assert result["needs"][0]["need_type"] == "expand_learning_frontier"
    assert result["world_model"]["learning_momentum"] == 0.7


def test_drive_judgement_owner_falls_back_to_matching_or_first_intents():
    perception, world_model, reflection, policy, needs, intents = _inputs()

    result = build_drive_judgement_metadata(
        intent=None,
        candidate_kind="missing_kind",
        all_intents=intents,
        needs=needs,
        perception=perception,
        world_model=world_model,
        reflection=reflection,
        adaptive_policy=policy,
    )

    assert result["intent"]["candidate_kind"] == "exploratory_learning"
    assert result["intents"][0]["candidate_kind"] == "exploratory_learning"

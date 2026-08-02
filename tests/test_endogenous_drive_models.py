from systems.supervisor.endogenous_drive_models import (
    DriveAdaptivePolicy,
    DriveDeliberationReport,
    DriveIntent,
    DrivePerceptionSnapshot,
    DriveReflection,
    DriveSignal,
    DriveWorldModel,
)
from systems.supervisor.endogenous_needs import DriveNeed


def test_drive_models_serialize_nested_deliberation_contract():
    report = DriveDeliberationReport(
        perception=DrivePerceptionSnapshot(
            user_mode="user_chain_quiet",
            autonomous_chain_gate_active=False,
            system_posture="stable",
            active_sessions=0,
            recent_errors=0,
            uncertainty_count=0,
            correction_signals=0,
            learning_quality=80.12345,
            has_learning_history=True,
            shell_slot_present=True,
            shell_slot_id="slot-B",
            api_b_judgement_count=1,
            learning_backlog_count=0,
            body_improvement_backlog_count=0,
            stale_backlog_count=0,
            pending_review_count=0,
            api_a_ready_count=2,
        ),
        world_model=DriveWorldModel(
            user_mode="user_chain_quiet",
            system_posture="stable",
            truthfulness_pressure=0.4,
            learning_momentum=0.6,
            body_upgrade_readiness=0.7,
            governance_load_state="clear",
            memory_pressure=0.3,
            self_confidence=0.8,
        ),
        reflection=DriveReflection(
            recent_learning_count=1,
            recent_learning_quality=0.8,
            learning_yield_state="strong",
            api_b_judgement_blockage_pressure=0.1,
            api_b_judgement_blockage_state="clear",
            body_growth_blocked=False,
            repeated_drive_pressure=0.0,
            autonomy_readiness=0.7,
            dominant_constraint="none",
            rationale="ready.",
        ),
        adaptive_policy=DriveAdaptivePolicy(
            learning_expansion_bias=0.6,
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
        ),
        needs=[
            DriveNeed(
                need_type="expand_learning_frontier",
                severity=0.7,
                urgency=0.6,
                confidence=0.8,
                rationale="learn.",
            )
        ],
        intents=[
            DriveIntent(
                intent_type="expand_learning",
                priority=0.7,
                rationale="learn.",
                target_horizon="next_cycle",
                output_channel="api_b",
                source_needs=["expand_learning_frontier"],
                candidate_kind="exploratory_learning",
            )
        ],
        signals=[
            DriveSignal(
                signal_type="learning",
                priority=0.7,
                message="learn",
                rationale="learn.",
                related_intent="expand_learning",
            )
        ],
    )

    payload = report.to_dict()

    assert payload["perception"]["learning_quality"] == 80.1235
    assert payload["perception"]["api_a_handoff_count"] == 2
    assert payload["needs"][0]["need_type"] == "expand_learning_frontier"
    assert payload["intents"][0]["candidate_kind"] == "exploratory_learning"
    assert payload["signals"][0]["related_intent"] == "expand_learning"

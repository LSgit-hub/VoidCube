from types import SimpleNamespace

from voidcube.systems.supervisor.endogenous_drive_state import (
    build_drive_perception_projection,
    build_drive_world_model_projection,
)


def test_drive_perception_projection_preserves_posture_and_activity_boundaries():
    result = build_drive_perception_projection(
        drive_input={
            "autonomous_chain_gate_active": True,
            "user_chain_signal": {"is_quiet": False},
            "completed_learning_tasks": [{"quality_score": 1.0}],
            "shell_slot": {"slot_id": "slot-B"},
            "checks": {"memory": "ok"},
            "idle_seconds": {"user": 12},
        },
        activity={"active_sessions": 0},
        drive_context={
            "learning_backlog_titles": ["one"],
            "body_improvement_backlog_titles": ["body"],
            "stale_backlog_count": 2,
            "pending_review_count": 1,
            "api_b_judgement_count": 3,
            "employee_dispatch_count": 2,
            "employee_running_count": 1,
        },
        counts={"error_count": 2, "uncertainty_high_count": 1},
        correction_signals=3,
        shell_slot_meta={"slot_id": "slot-B"},
    )

    assert result["user_mode"] == "autonomous_chain_gate"
    assert result["system_posture"] == "degrading"
    assert result["learning_quality"] == 80.0
    assert result["employee_dispatch_count"] == 2
    assert result["employee_dispatch_count"] == 2
    assert result["checks"] == {"memory": "ok"}


def test_drive_perception_projection_uses_growth_window_when_idle_and_ready():
    result = build_drive_perception_projection(
        drive_input={"completed_learning_tasks": [{"quality_score": 1.0}]},
        activity={"active_sessions": 0},
        drive_context={},
        counts={},
        correction_signals=0,
        shell_slot_meta={"worktree_path": "body/slot-B"},
    )

    assert result["user_mode"] == "user_chain_quiet"
    assert result["system_posture"] == "growth_window"
    assert result["shell_slot_present"] is True


def test_drive_world_model_projection_preserves_load_and_confidence_rules():
    perception = SimpleNamespace(
        user_mode="user_chain_quiet",
        system_posture="degrading",
        correction_signals=4,
        learning_quality=50.0,
        has_learning_history=True,
        learning_backlog_count=1,
        shell_slot_present=True,
        body_improvement_backlog_count=1,
        api_b_judgement_count=4,
        stale_backlog_count=1,
        pending_review_count=1,
        autonomous_chain_gate_active=True,
        active_sessions=0,
    )

    result = build_drive_world_model_projection(perception)

    assert result["truthfulness_pressure"] == 0.65
    assert result["governance_load_state"] == "busy"
    assert result["learning_momentum"] == 0.42
    assert result["body_upgrade_readiness"] == 0.3
    assert result["self_confidence"] == 0.59

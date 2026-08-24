from __future__ import annotations

from datetime import datetime

from voidcube.systems.supervisor.drive_input_evaluation import (
    DriveInputEvaluationConfig,
    evaluate_drive_input_snapshot,
)


def _config() -> DriveInputEvaluationConfig:
    return DriveInputEvaluationConfig(
        gateway_address="http://gateway",
        now=datetime(2026, 5, 25, 0, 15),
        user_idle_seconds=600,
        memory_idle_seconds=600,
        workflow_idle_seconds=600,
        perception_scope="full",
        autonomous_chain_gate_active=False,
        evidence_packet={},
    )


def _profile(task_family: str = "general_self_evolution") -> dict[str, str]:
    return {
        "governance_task_type": "self_evolution",
        "task_family": task_family,
        "execution_kind": task_family,
    }


def test_drive_input_evaluation_is_independent_of_supervisor_host() -> None:
    result = evaluate_drive_input_snapshot(
        request={},
        snapshot={
            "last_user_request_at": "2026-05-25T00:00:00",
            "last_memory_task_at": "2026-05-25T00:00:00",
            "last_self_learning_activity_at": "2026-05-25T00:00:00",
            "last_autonomous_chain_plan_at": "2026-05-25T00:00:00",
            "last_autonomous_chain_execute_at": "2026-05-25T00:00:00",
            "last_autonomous_chain_activity_at": "2026-05-25T00:00:00",
            "active_sessions": 0,
            "counts": {},
        },
        config=_config(),
        task_profile=_profile(),
        shell_slot=None,
        completed_learning_tasks=[],
    )

    assert result["decisions"] == {
        "eligible_for_planning": True,
        "eligible_for_execution": True,
    }
    assert result["task_profile"] == _profile()


def test_drive_input_evaluation_blocks_execution_for_active_supervisor_task() -> None:
    result = evaluate_drive_input_snapshot(
        request={},
        snapshot={
            "last_memory_task_at": "2026-05-25T00:00:00",
            "last_autonomous_chain_plan_at": "2026-05-25T00:00:00",
            "last_autonomous_chain_execute_at": "2026-05-25T00:14:30",
            "last_autonomous_chain_activity_at": "2026-05-25T00:14:30",
            "active_sessions": 0,
            "active_cli_executor": {
                "agent_lane": "supervisor_task",
                "lease_status": "healthy",
                "idle_seconds": 30,
            },
            "counts": {},
        },
        config=_config(),
        task_profile=_profile(),
        shell_slot=None,
        completed_learning_tasks=[],
    )

    assert result["checks"]["has_employee_execution_idle"] is False
    assert result["decisions"]["eligible_for_execution"] is False

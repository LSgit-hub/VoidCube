from voidcube.systems.supervisor.ui_autonomous_projection import (
    project_autonomous_observation,
)


def test_dispatched_auto_task_reports_stale_employee_executor():
    observation = project_autonomous_observation(
        [
            {
                "task_id": "auto-1",
                "title": "Review endogenous cognition",
                "status": "approved",
                "governance_task_type": "self_learning",
                "execution_kind": "employee",
                "created_at": "2026-08-21T00:00:00+00:00",
            }
        ],
        drive_candidates=[],
        active_cli_executor={
            "agent_lane": "supervisor_task",
            "lease_status": "stale",
            "is_stale": True,
        },
    )

    stage = next(
        item
        for item in observation["loop"]["stage_cards"]
        if item.get("stage_key") == "employee_execution"
    )
    assert stage["status"] == "stale"
    assert stage["status_label"] == "执行器失联"
    assert "失联" in stage["summary"]


def test_dispatched_auto_task_reports_ready_with_live_employee_executor():
    observation = project_autonomous_observation(
        [
            {
                "task_id": "auto-1",
                "title": "Review endogenous cognition",
                "status": "approved",
                "governance_task_type": "self_learning",
                "execution_kind": "employee",
                "created_at": "2026-08-21T00:00:00+00:00",
            }
        ],
        drive_candidates=[],
        active_cli_executor={
            "agent_lane": "supervisor_task",
            "lease_status": "active",
            "is_stale": False,
        },
    )

    stage = next(
        item
        for item in observation["loop"]["stage_cards"]
        if item.get("stage_key") == "employee_execution"
    )
    assert stage["status"] == "ready"
    assert stage["status_label"] == "待接手"

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from voidcube.systems.supervisor.autonomous_employee_dispatch_service import (
    AutonomousEmployeeDispatchService,
)


def _service(schedule_store):
    return AutonomousEmployeeDispatchService(
        task_state=SimpleNamespace(update_metadata=Mock()),
        task_store=SimpleNamespace(list_employee_execution_lane_tasks=Mock(return_value=[])),
        scheduled_task_store=schedule_store,
        task_profile_policy=SimpleNamespace(
            governance_type=lambda task: task.governance_task_type,
            execution_kind=lambda task: task.execution_kind,
            runtime_family=lambda task: task.task_family,
        ),
        resolve_worker_role=lambda role: f"{role}-employee",
        touch_gateway_activity=AsyncMock(),
        record_ui_activity=Mock(),
    )


def _task(**overrides):
    values = dict(
        task_id="task-1",
        title="Research task",
        summary="Inspect the canonical implementation.",
        task_type="self_learning",
        governance_task_type="self_learning",
        task_family="self_learning",
        execution_kind="self_learning",
        evidence={"source": "api-b"},
        constraints={"allowed_paths": ["src/voidcube/runtime/agent/"]},
        metadata={},
        execution_request=None,
        status="approved",
    )
    values.update(overrides)
    return SimpleNamespace(**values)


def test_dispatch_creates_idempotent_employee_assignment():
    schedule = {
        "schedule_id": "employee-1",
        "worker_role": "research-employee",
        "autonomous_task_id": "task-1",
        "created_at": "2026-08-19T00:00:00+00:00",
    }
    store = SimpleNamespace(
        list=Mock(side_effect=[[], [schedule]]),
        create=Mock(return_value=schedule),
    )
    service = _service(store)
    task = _task()

    first = service.dispatch(task)
    second = service.dispatch(task)

    assert first == {
        "status": "dispatched",
        "employee_task_id": "employee-1",
        "worker_role": "research-employee",
    }
    assert second["status"] == "already_dispatched"
    store.create.assert_called_once()
    payload = store.create.call_args.args[0]
    assert payload["created_by"] == "api_b"
    assert payload["requested_via"] == "autonomous_worker"
    assert "不得再调用或转交给 API-A" in payload["instruction"]


@pytest.mark.asyncio
async def test_reconcile_projects_employee_run_back_to_task():
    task = _task(status="approved")
    state = SimpleNamespace(
        update_status=Mock(return_value=SimpleNamespace(task_id="task-1", status="completed")),
        update_metadata=Mock(),
    )
    store = SimpleNamespace(
        list=Mock(
            return_value=[
                {
                    "schedule_id": "employee-1",
                    "autonomous_task_id": "task-1",
                    "worker_role": "research-employee",
                    "created_at": "2026-08-19T00:00:00+00:00",
                }
            ]
        ),
        recent_runs=Mock(
            return_value=[
                {
                    "schedule_id": "employee-1",
                    "run_id": "run-1",
                    "status": "completed",
                    "result_summary": "Validated the canonical path.",
                }
            ]
        ),
    )
    service = _service(store)
    service._task_state = state
    service._task_store = SimpleNamespace(
        list_employee_execution_lane_tasks=Mock(return_value=[task])
    )

    updates = await service.reconcile()

    assert updates == [{"task_id": "task-1", "status": "completed"}]
    state.update_metadata.assert_called_once()
    state.update_status.assert_called_once()
    assert state.update_status.call_args.kwargs["actor"] == "employee_agent"

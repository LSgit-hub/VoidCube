from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from voidcube.systems.supervisor.autonomous_employee_dispatch_service import (
    AutonomousEmployeeDispatchService,
)


def _service(schedule_store, *, review_body_improvement=None):
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
        review_body_improvement=review_body_improvement,
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


@pytest.mark.asyncio
async def test_reconcile_returns_auto_employee_result_to_supervisor_without_memory_access():
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
                    "result_summary": "Primary-source conclusion.",
                }
            ]
        ),
    )
    on_employee_result = AsyncMock()
    service = AutonomousEmployeeDispatchService(
        task_state=state,
        task_store=SimpleNamespace(
            list_employee_execution_lane_tasks=Mock(return_value=[task])
        ),
        scheduled_task_store=store,
        task_profile_policy=SimpleNamespace(
            governance_type=lambda current: current.governance_task_type,
            execution_kind=lambda current: current.execution_kind,
            runtime_family=lambda current: current.task_family,
        ),
        resolve_worker_role=lambda role: f"{role}-employee",
        touch_gateway_activity=AsyncMock(),
        record_ui_activity=Mock(),
        on_employee_result=on_employee_result,
    )

    updates = await service.reconcile()

    assert updates == [{"task_id": "task-1", "status": "completed"}]
    on_employee_result.assert_awaited_once()
    returned_task, result_context, final_status = on_employee_result.await_args.args
    assert returned_task.status == "completed"
    assert final_status == "completed"
    assert result_context["result_summary"] == "Primary-source conclusion."
    assert state.update_status.call_args.kwargs["context"]["employee_final_response"] == (
        "Primary-source conclusion."
    )


@pytest.mark.asyncio
async def test_reconcile_repairs_approved_task_without_employee_assignment():
    task = _task(status="approved")
    schedule = {
        "schedule_id": "employee-repaired",
        "worker_role": "research-employee",
        "autonomous_task_id": "task-1",
        "created_at": "2026-08-22T00:00:00+00:00",
    }
    state = SimpleNamespace(update_metadata=Mock())
    store = SimpleNamespace(
        list=Mock(return_value=[]),
        create=Mock(return_value=schedule),
        recent_runs=Mock(return_value=[]),
    )
    service = _service(store)
    service._task_state = state
    service._task_store = SimpleNamespace(
        list_employee_execution_lane_tasks=Mock(return_value=[task])
    )

    updates = await service.reconcile()

    assert updates == [
        {
            "task_id": "task-1",
            "status": "approved",
            "employee_task_id": "employee-repaired",
        }
    ]
    store.create.assert_called_once()
    state.update_metadata.assert_called_once()


@pytest.mark.asyncio
async def test_reconcile_migrates_reconciling_task_before_repairing_assignment():
    task = _task(status="reconciling")
    migrated = _task(status="approved")
    schedule = {
        "schedule_id": "employee-migrated",
        "worker_role": "research-employee",
        "autonomous_task_id": "task-1",
        "created_at": "2026-08-22T00:00:00+00:00",
    }
    state = SimpleNamespace(
        update_status=Mock(return_value=migrated),
        update_metadata=Mock(),
    )
    store = SimpleNamespace(
        list=Mock(return_value=[]),
        create=Mock(return_value=schedule),
        recent_runs=Mock(return_value=[]),
    )
    service = _service(store)
    service._task_state = state
    service._task_store = SimpleNamespace(
        list_employee_execution_lane_tasks=Mock(return_value=[task])
    )

    updates = await service.reconcile()

    assert updates[0]["employee_task_id"] == "employee-migrated"
    state.update_status.assert_called_once_with(
        "task-1",
        status="approved",
        actor="employee_dispatch_migration",
        reason="历史执行租约状态已迁移为员工代理派工状态。",
        context={"migration": "employee_reconciling_to_employee_dispatch"},
        event_type="employee_dispatch_migration",
    )
    store.create.assert_called_once()


@pytest.mark.asyncio
async def test_reconcile_recovers_approved_task_with_terminal_legacy_run():
    lease = SimpleNamespace(state="reconciling", generation=2, attempt_id="attempt-2")
    task = _task(status="approved", execution_lease=lease)
    running = _task(status="running", execution_lease=lease)
    completed = _task(status="completed", execution_lease=lease)
    state = SimpleNamespace(
        update_status=Mock(side_effect=[running, completed]),
        update_metadata=Mock(),
    )
    schedule = {
        "schedule_id": "employee-legacy",
        "autonomous_task_id": "task-1",
        "worker_role": "research",
        "created_at": "2026-08-22T00:00:00+00:00",
    }
    store = SimpleNamespace(
        list=Mock(return_value=[schedule]),
        recent_runs=Mock(
            return_value=[
                {
                    "schedule_id": "employee-legacy",
                    "run_id": "run-legacy",
                    "status": "completed",
                    "result_summary": "Recovered historical result.",
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
    assert state.update_status.call_args_list[0].kwargs["actor"] == (
        "employee_dispatch_recovery"
    )
    assert state.update_status.call_args_list[1].kwargs["actor"] == "employee_agent"


@pytest.mark.asyncio
async def test_reconcile_does_not_run_body_review_for_learning_task():
    task = _task(status="running")
    state = SimpleNamespace(
        update_metadata=Mock(),
        update_status=Mock(return_value=SimpleNamespace(task_id="task-1", status="completed")),
    )
    schedule = {
        "schedule_id": "employee-1",
        "autonomous_task_id": "task-1",
        "worker_role": "research",
        "created_at": "2026-08-19T00:00:00+00:00",
    }
    store = SimpleNamespace(
        list=Mock(return_value=[schedule]),
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
    review = AsyncMock()
    service = _service(store, review_body_improvement=review)
    service._task_state = state
    service._task_store = SimpleNamespace(
        list_employee_execution_lane_tasks=Mock(return_value=[task])
    )

    updates = await service.reconcile()

    assert updates == [{"task_id": "task-1", "status": "completed"}]
    review.assert_not_awaited()


def test_body_improvement_result_requires_lineage_verification_and_lease():
    service = _service(SimpleNamespace())
    task = _task(
        task_family="body_upgrade",
        execution_kind="body_improvement",
        status="running",
        execution_lease=SimpleNamespace(
            generation=2,
            attempt_id="attempt-2",
            state="active",
        ),
    )
    valid = {
        "body_improvement_report": {
            "task_id": "task-1",
            "lease_generation": 2,
            "attempt_id": "attempt-2",
            "baseline_commit": "a" * 40,
            "commit_hash": "b" * 40,
            "changed_files": ["src/voidcube/runtime/agent/runner.py"],
            "verification": {"passed": True, "checks": ["pytest"]},
        }
    }

    accepted = service._validate_body_improvement_result(task, json.dumps(valid))
    missing = service._validate_body_improvement_result(task, "completed")
    stale = service._validate_body_improvement_result(
        task,
        json.dumps(
            {
                **valid,
                "body_improvement_report": {
                    **valid["body_improvement_report"],
                    "lease_generation": 1,
                },
            }
        ),
    )

    assert accepted["ok"] is True
    assert missing["reject_reason"] == "body_improvement_result_must_be_json"
    assert stale["reject_reason"] == "body_improvement_lease_mismatch"


@pytest.mark.asyncio
async def test_reconcile_rejects_body_completion_without_evidence():
    task = _task(
        status="running",
        task_family="body_upgrade",
        execution_kind="body_improvement",
        execution_lease=SimpleNamespace(
            generation=2,
            attempt_id="attempt-2",
            state="active",
        ),
    )
    state = SimpleNamespace(
        update_metadata=Mock(),
        finalize_execution=Mock(
            return_value=SimpleNamespace(task_id="task-1", status="failed")
        ),
    )
    store = SimpleNamespace(
        list=Mock(
            return_value=[
                {
                    "schedule_id": "employee-1",
                    "autonomous_task_id": "task-1",
                    "worker_role": "coding",
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
                    "result_summary": "completed without report",
                }
            ]
        ),
    )
    service = _service(store)
    service._task_state = state
    service._task_store = SimpleNamespace(
        list_employee_execution_lane_tasks=Mock(return_value=[task]),
        get_task=Mock(return_value=task),
    )

    updates = await service.reconcile()

    assert updates == [{"task_id": "task-1", "status": "failed"}]
    assert state.finalize_execution.call_args.kwargs["status"] == "failed"
    assert (
        state.finalize_execution.call_args.kwargs["context"]["evidence_validation"][
            "reject_reason"
        ]
        == "body_improvement_result_must_be_json"
    )


@pytest.mark.asyncio
async def test_reconcile_runs_governed_body_review_before_completion():
    task = _task(
        status="running",
        task_family="body_upgrade",
        execution_kind="body_improvement",
        execution_lease=SimpleNamespace(
            generation=2,
            attempt_id="attempt-2",
            state="active",
        ),
    )
    state = SimpleNamespace(
        update_metadata=Mock(),
        finalize_execution=Mock(
            return_value=SimpleNamespace(task_id="task-1", status="completed")
        ),
    )
    schedule = {
        "schedule_id": "employee-1",
        "autonomous_task_id": "task-1",
        "worker_role": "coding",
        "created_at": "2026-08-19T00:00:00+00:00",
    }
    report = {
        "body_improvement_report": {
            "task_id": "task-1",
            "lease_generation": 2,
            "attempt_id": "attempt-2",
            "slot_id": "slot-b",
            "baseline_commit": "a" * 40,
            "commit_hash": "b" * 40,
            "changed_files": ["src/voidcube/runtime/agent/runner.py"],
            "execution_environment": {
                "validation_scope": "container",
                "image": "python:3.12",
            },
            "verification": {"passed": True},
        }
    }
    review = AsyncMock(return_value={"score_delta": 4})
    store = SimpleNamespace(
        list=Mock(return_value=[schedule]),
        recent_runs=Mock(
            return_value=[
                {
                    "schedule_id": "employee-1",
                    "run_id": "run-1",
                    "status": "completed",
                    "result_summary": json.dumps(report),
                }
            ]
        ),
    )
    service = _service(store, review_body_improvement=review)
    service._task_state = state
    service._task_store = SimpleNamespace(
        list_employee_execution_lane_tasks=Mock(return_value=[task])
    )

    updates = await service.reconcile()

    assert updates == [{"task_id": "task-1", "status": "completed"}]
    review.assert_awaited_once()
    assert review.await_args.args[0]["slot_id"] == "slot-b"
    assert state.finalize_execution.call_args.kwargs["status"] == "completed"
    assert (
        state.finalize_execution.call_args.kwargs["context"]["evidence_validation"][
            "review_validation"
        ]["review"]["score_delta"]
        == 4
    )


@pytest.mark.asyncio
async def test_reconcile_rejects_body_completion_when_governed_review_rejects():
    task = _task(
        status="running",
        task_family="body_upgrade",
        execution_kind="body_improvement",
        execution_lease=SimpleNamespace(
            generation=2,
            attempt_id="attempt-2",
            state="active",
        ),
    )
    state = SimpleNamespace(
        update_metadata=Mock(),
        finalize_execution=Mock(
            return_value=SimpleNamespace(task_id="task-1", status="failed")
        ),
    )
    schedule = {
        "schedule_id": "employee-1",
        "autonomous_task_id": "task-1",
        "worker_role": "coding",
        "created_at": "2026-08-19T00:00:00+00:00",
    }
    report = {
        "task_id": "task-1",
        "lease_generation": 2,
        "attempt_id": "attempt-2",
        "slot_id": "slot-b",
        "baseline_commit": "a" * 40,
        "commit_hash": "b" * 40,
        "changed_files": ["src/voidcube/runtime/agent/runner.py"],
        "verification": {"passed": True},
    }
    store = SimpleNamespace(
        list=Mock(return_value=[schedule]),
        recent_runs=Mock(
            return_value=[
                {
                    "schedule_id": "employee-1",
                    "run_id": "run-1",
                    "status": "completed",
                    "result_summary": json.dumps(report),
                }
            ]
        ),
    )
    review = AsyncMock(return_value={"score_delta": 0, "reject_reason": "commit_not_found"})
    service = _service(store, review_body_improvement=review)
    service._task_state = state
    service._task_store = SimpleNamespace(
        list_employee_execution_lane_tasks=Mock(return_value=[task])
    )

    updates = await service.reconcile()

    assert updates == [{"task_id": "task-1", "status": "failed"}]
    assert state.finalize_execution.call_args.kwargs["status"] == "failed"
    assert (
        state.finalize_execution.call_args.kwargs["context"]["evidence_validation"][
            "review_validation"
        ]["reject_reason"]
        == "commit_not_found"
    )

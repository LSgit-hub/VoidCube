from __future__ import annotations

from pathlib import Path

import pytest

from systems.supervisor.supervisor import Supervisor, SupervisorConfig, SupervisorExecutionConfig


def _make_supervisor(tmp_path: Path) -> Supervisor:
    return Supervisor(
        SupervisorConfig(
            execution=SupervisorExecutionConfig(git_repo_path=str(tmp_path))
        )
    )


@pytest.mark.asyncio
@pytest.mark.unit
async def test_supervisor_activity_guard_allows_planning_and_execution_when_gateway_is_idle(tmp_path):
    supervisor = _make_supervisor(tmp_path)

    async def fake_snapshot():
        return {
            "last_user_request_at": "2026-05-25T00:00:00",
            "last_agent_work_at": "2026-05-25T00:00:00",
            "last_memory_task_at": "2026-05-25T00:00:00",
            "last_self_evolution_activity_at": "2026-05-25T00:00:00",
            "counts": {},
            "active_sessions": 0,
        }

    supervisor._fetch_gateway_activity_snapshot = fake_snapshot  # type: ignore[method-assign]

    result = await supervisor.evaluate_activity_guards(
        {
            "now": "2026-05-25T00:15:00",
        }
    )

    assert result["governance_task_type"] == "self_evolution"
    assert result["task_family"] == "general_self_evolution"
    assert result["execution_kind"] == "general_self_evolution"
    assert result["task_profile"] == {
        "governance_task_type": "self_evolution",
        "task_family": "general_self_evolution",
        "execution_kind": "general_self_evolution",
    }
    assert result["governance_task_type_decisions"]["self_evolution"] == result["decisions"]
    assert result["user_chain_signal"]["is_quiet"] is True
    assert result["user_chain_signal"]["scope"] == "soft_signal_only"
    assert result["decisions"]["eligible_for_planning"] is True
    assert result["decisions"]["eligible_for_execution"] is True


@pytest.mark.asyncio
@pytest.mark.unit
async def test_supervisor_activity_guard_compares_gateway_naive_timestamps_as_utc(tmp_path):
    supervisor = _make_supervisor(tmp_path)

    async def fake_snapshot():
        return {
            "last_user_request_at": "2026-05-25T00:00:00",
            "last_agent_work_at": "2026-05-25T00:00:00",
            "last_memory_task_at": "2026-05-25T00:00:00",
            "last_self_evolution_activity_at": "2026-05-25T00:00:00",
            "counts": {},
            "active_sessions": 0,
        }

    supervisor._fetch_gateway_activity_snapshot = fake_snapshot  # type: ignore[method-assign]

    result = await supervisor.evaluate_activity_guards(
        {
            "now": "2026-05-25T08:15:00+08:00",
        }
    )

    assert result["idle_seconds"]["user"] == 900.0
    assert result["idle_seconds"]["agent"] == 900.0
    assert result["idle_seconds"]["memory"] == 900.0


@pytest.mark.asyncio
@pytest.mark.unit
async def test_supervisor_activity_guard_uses_configured_thresholds_and_reports_cli_lease(tmp_path):
    supervisor = _make_supervisor(tmp_path)
    supervisor.config = supervisor.config.model_copy(
        update={
            "service_runtime": supervisor.config.service_runtime.model_copy(
                update={
                    "activity_guard_user_seconds": 120,
                    "activity_guard_memory_seconds": 240,
                    "activity_guard_workflow_seconds": 300,
                }
            )
        }
    )

    async def fake_snapshot():
        return {
            "last_user_request_at": "2026-05-25T00:04:00",
            "last_agent_work_at": "2026-05-25T00:00:00",
            "last_memory_task_at": "2026-05-25T00:02:00",
            "last_self_evolution_activity_at": "2026-05-25T00:02:00",
            "counts": {},
            "active_sessions": 1,
            "active_cli_executor": {
                "session_id": "cli-1",
                "stale_after_seconds": 90,
                "lease_status": "stale",
            },
        }

    supervisor._fetch_gateway_activity_snapshot = fake_snapshot  # type: ignore[method-assign]

    result = await supervisor.evaluate_activity_guards({"now": "2026-05-25T00:05:00"})

    assert result["thresholds"]["user_idle_seconds"] == 120
    assert result["thresholds"]["memory_idle_seconds"] == 240
    assert result["thresholds"]["workflow_idle_seconds"] == 300
    assert result["thresholds"]["active_cli_stale_after_seconds"] == 90
    assert result["user_chain_signal"]["is_quiet"] is False
    assert result["checks"]["has_agent_idle"] is True


@pytest.mark.asyncio
@pytest.mark.unit
async def test_supervisor_activity_guard_blocks_execution_when_recent_workflow_activity_exists(tmp_path):
    supervisor = _make_supervisor(tmp_path)

    async def fake_snapshot():
        return {
            "last_user_request_at": "2026-05-25T00:12:00",
            "last_agent_work_at": "2026-05-25T00:12:00",
            "last_memory_task_at": None,
            "last_self_evolution_activity_at": None,
            "counts": {},
            "active_sessions": 1,
        }

    supervisor._fetch_gateway_activity_snapshot = fake_snapshot  # type: ignore[method-assign]

    result = await supervisor.evaluate_activity_guards(
        {
            "now": "2026-05-25T00:15:00",
        }
    )

    assert result["user_chain_signal"]["is_quiet"] is False
    assert result["checks"]["has_agent_idle"] is False
    assert result["decisions"]["eligible_for_planning"] is True
    assert result["decisions"]["eligible_for_execution"] is False


@pytest.mark.asyncio
@pytest.mark.unit
async def test_supervisor_activity_guard_allows_self_learning_followup_outside_execution_window_when_idle(tmp_path):
    supervisor = _make_supervisor(tmp_path)

    async def fake_snapshot():
        return {
            "last_user_request_at": "2026-05-25T11:40:00",
            "last_agent_work_at": "2026-05-25T11:40:00",
            "last_memory_task_at": "2026-05-25T11:40:00",
            "last_self_learning_activity_at": "2026-05-25T11:40:00",
            "last_self_evolution_activity_at": "2026-05-25T11:40:00",
            "counts": {},
            "active_sessions": 0,
        }

    supervisor._fetch_gateway_activity_snapshot = fake_snapshot  # type: ignore[method-assign]

    result = await supervisor.evaluate_activity_guards(
        {
            "now": "2026-05-25T12:00:00",
            "task_family": "self_learning",
        }
    )

    assert result["governance_task_type"] == "self_learning"
    assert result["task_family"] == "self_learning"
    assert result["execution_kind"] is None
    assert result["governance_task_type_decisions"]["self_learning"] == result["decisions"]
    assert result["user_chain_signal"]["scope"] == "soft_signal_only"
    assert result["checks"]["has_self_learning_idle"] is True
    assert result["decisions"]["eligible_for_planning"] is True
    assert result["decisions"]["eligible_for_execution"] is True


@pytest.mark.asyncio
@pytest.mark.unit
async def test_supervisor_activity_guard_blocks_memory_maintenance_when_recent_memory_activity_exists(tmp_path):
    supervisor = _make_supervisor(tmp_path)

    async def fake_snapshot():
        return {
            "last_user_request_at": "2026-05-25T00:00:00",
            "last_agent_work_at": "2026-05-25T00:00:00",
            "last_memory_task_at": "2026-05-25T00:14:30",
            "last_self_learning_activity_at": "2026-05-25T00:00:00",
            "last_self_evolution_activity_at": "2026-05-25T00:00:00",
            "counts": {},
            "active_sessions": 0,
        }

    supervisor._fetch_gateway_activity_snapshot = fake_snapshot  # type: ignore[method-assign]

    result = await supervisor.evaluate_activity_guards(
        {
            "now": "2026-05-25T00:15:00",
            "task_family": "memory_maintenance",
        }
    )

    assert result["governance_task_type"] == "memory_maintenance"
    assert result["task_family"] == "memory_maintenance"
    assert result["execution_kind"] == "memory_maintenance"
    assert result["governance_task_type_decisions"]["memory_maintenance"] == result["task_family_decisions"]["memory_maintenance"]
    assert result["user_chain_signal"]["is_quiet"] is True
    assert result["checks"]["has_memory_idle"] is False
    assert result["decisions"]["eligible_for_execution"] is False


@pytest.mark.asyncio
@pytest.mark.unit
async def test_supervisor_activity_guard_exposes_body_switch_family_without_collapsing_it_to_generic_self_evolution(tmp_path):
    supervisor = _make_supervisor(tmp_path)

    async def fake_snapshot():
        return {
            "last_user_request_at": "2026-05-25T00:00:00",
            "last_agent_work_at": "2026-05-25T00:00:00",
            "last_memory_task_at": "2026-05-25T00:00:00",
            "last_self_learning_activity_at": "2026-05-25T00:00:00",
            "last_self_evolution_plan_at": "2026-05-25T00:00:00",
            "last_self_evolution_execute_at": "2026-05-25T00:00:00",
            "last_self_evolution_activity_at": "2026-05-25T00:00:00",
            "counts": {},
            "active_sessions": 0,
        }

    supervisor._fetch_gateway_activity_snapshot = fake_snapshot  # type: ignore[method-assign]

    result = await supervisor.evaluate_activity_guards(
        {
            "now": "2026-05-25T00:15:00",
            "task_family": "body_switch",
        }
    )

    assert result["governance_task_type"] == "self_evolution"
    assert result["task_family"] == "body_switch"
    assert result["execution_kind"] == "body_switch"
    assert result["task_profile"] == {
        "governance_task_type": "self_evolution",
        "task_family": "body_switch",
        "execution_kind": "body_switch",
    }
    assert result["governance_task_type_decisions"]["self_evolution"] == result["decisions"]
    assert result["task_family_decisions"]["body_switch"] == result["decisions"]



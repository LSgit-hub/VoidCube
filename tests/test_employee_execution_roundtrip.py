"""Exercise employee claim, response loss, execution, and durable result recovery."""

import asyncio
import threading
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest

from voidcube.application.scheduling.scheduled_execution_host import ScheduledExecutionHost
from voidcube.application.scheduling.scheduled_executor import (
    ScheduledRequestRejected,
    ScheduledTaskExecutorPorts,
    ScheduledTaskExecutorRuntime,
)
from voidcube.infrastructure.persistence.scheduled_writeback import SqliteScheduledWritebackOutbox
from voidcube.systems.supervisor.autonomous_chain_store import AutonomousChainStore
from voidcube.systems.supervisor.autonomous_employee_dispatch_service import AutonomousEmployeeDispatchService
from voidcube.systems.supervisor.autonomous_task_state import AutonomousTaskStateService
from voidcube.systems.supervisor.scheduled_tasks import ScheduledTaskRuntimeMixin, ScheduledTaskStore
from voidcube.systems.supervisor.task_profile_policy import TaskProfilePolicy


@pytest.mark.parametrize("role", ["general", "research", "coding", "media"])
@pytest.mark.parametrize("worker_fails", [False, True])
def test_employee_lost_claim_response_replays_and_returns_real_result(tmp_path, monkeypatch, role, worker_fails):
    chain = AutonomousChainStore(tmp_path / "chain.json")
    schedules = ScheduledTaskStore(tmp_path / "scheduled.db")
    governance = Mock()
    state = AutonomousTaskStateService(store=chain, governance_repository=governance)
    task = state.create_task(title="Employee diagnostic", task_type="user", source="companion")
    state.update_status(task.task_id, status="approved", actor="api_b")
    schedules.create({
        "title": task.title, "instruction": "Return a diagnostic receipt",
        "schedule_type": "once", "run_at": datetime.now(timezone.utc).isoformat(),
        "created_by": "api_b", "requested_via": "companion_delegate",
        "worker_role": role, "autonomous_task_id": task.task_id,
    })

    class Supervisor(ScheduledTaskRuntimeMixin):
        _scheduled_task_store = schedules
        _autonomous_task_state = state
        _service_runtime = SimpleNamespace(autonomous_chain_gate_active=False)
        _provider_pool_service = SimpleNamespace(dispatch_policy=lambda: {})

    supervisor = Supervisor()
    requests = []
    finished = threading.Event()
    runs = []
    receipts = []

    class Agent:
        def run_conversation(self, **kwargs):
            runs.append(kwargs)
            if worker_fails:
                raise RuntimeError("employee tool failed")
            return {"final_response": "employee evidence", "error": ""}

    def post(path, payload):
        if path == "/scheduled-tasks/claim":
            requests.append(dict(payload))
            response = asyncio.run(supervisor.claim_scheduled_task(payload))
            if len(requests) == 1:
                raise ScheduledRequestRejected(503, "response lost after server commit")
            return response
        if path.endswith("/finish"):
            response = asyncio.run(supervisor.finish_scheduled_task_run(path.split("/")[2], payload))
            receipts.append(dict(payload))
            finished.set()
            if len(receipts) == 1:
                raise ScheduledRequestRejected(503, "finish response lost after server commit")
            return response
        raise AssertionError(path)

    host = ScheduledExecutionHost(
        ensure_credentials=lambda: True,
        resolve_agent_route=lambda _prompt, _role: {
            "model": "diagnostic-model", "runtime": {"provider": "diagnostic"},
        },
        create_agent=lambda *_args, **_kwargs: Agent(),
        completion_outcome=lambda result: (not bool(result.get("error")), result["final_response"], result["error"]),
        announce_start=lambda *_args: None,
        # The employee receipt must survive a broken UI adapter.
        render_completion=Mock(side_effect=RuntimeError("UI unavailable")),
        invalidate=lambda: None,
    )
    outbox = SqliteScheduledWritebackOutbox(tmp_path / "outbox.db")
    runtime = ScheduledTaskExecutorRuntime(ScheduledTaskExecutorPorts(
        autonomous_mode_active=lambda: False, autonomous_mode_lock=None,
        execution_gate=None, get_session_id=lambda: "employee-owner",
        set_execution_active=Mock(), set_companion_active=Mock(),
        start_background_task=host.start, post_supervisor=post,
        rate_limit_metadata=lambda _error: {}, writeback_outbox=outbox,
    ))
    try:
        runtime.poll_workflow()
        original = chain.get_task(task.task_id).execution_lease
        runtime._last_poll_at = 0
        runtime.poll_workflow()
        assert finished.wait(5), "employee completion was not returned"
        with host._state._lock:
            threads = tuple(host._state._tasks.values())
        for thread in threads:
            thread.join(5)
            assert not thread.is_alive()
        assert requests[0]["requested_run_id"] == requests[1]["requested_run_id"]
        assert len(schedules.recent_runs()) == len(runs) == len(receipts) == 1
        assert chain.get_task(task.task_id).execution_lease.generation == original.generation
        assert receipts[0]["success"] is (not worker_fails)
        assert receipts[0]["error"] == ("employee tool failed" if worker_fails else "")
        assert outbox.pending_count() == 1
        import voidcube.infrastructure.persistence.scheduled_writeback as writeback
        delivery_time = writeback.time.time() + 120
        monkeypatch.setattr(writeback.time, "time", lambda: delivery_time)
        runtime._flush_writebacks()
        assert outbox.pending_count() == 0
        assert len(receipts) == 2
        assert receipts[0] == receipts[1]
        dispatch = AutonomousEmployeeDispatchService(
            task_state=state, task_store=chain, scheduled_task_store=schedules,
            task_profile_policy=TaskProfilePolicy(), resolve_worker_role=lambda role: role,
            touch_gateway_activity=AsyncMock(), record_ui_activity=Mock(),
        )
        asyncio.run(dispatch.reconcile())
        final = chain.get_task(task.task_id)
        assert final.status == ("failed" if worker_fails else "completed")
        assert final.metadata["employee_execution_result"]["employee_run_id"] == schedules.recent_runs()[0]["run_id"]
    finally:
        outbox.close()
        schedules.close()


def test_claim_replay_cannot_take_over_another_owner_or_expired_run(tmp_path):
    schedules = ScheduledTaskStore(tmp_path / "scheduled.db")
    now = datetime.now(timezone.utc)
    schedules.create({
        "title": "claim fence", "instruction": "do work", "schedule_type": "once",
        "run_at": now.isoformat(),
    })
    request_id = str(uuid4())
    schedules.claim_due(owner_session_id="owner", requested_run_id=request_id, now=now, lease_seconds=60)
    with pytest.raises(ValueError, match="owned and running"):
        schedules.claim_due(owner_session_id="other-owner", requested_run_id=request_id, now=now)
    with pytest.raises(ValueError, match="owned and running"):
        schedules.claim_due(owner_session_id="owner", requested_run_id=request_id, now=now + timedelta(seconds=61))
    schedules.close()


def test_scheduled_host_cancels_its_employee_agent():
    host = ScheduledExecutionHost(
        ensure_credentials=lambda: True, resolve_agent_route=Mock(), create_agent=Mock(),
        completion_outcome=Mock(), announce_start=Mock(), render_completion=Mock(), invalidate=Mock(),
    )
    agent = SimpleNamespace(interrupt=Mock())
    host._state.register_agent("employee-1", agent)
    assert host.cancel("employee-1", "cancelled by Xingzi") is True
    agent.interrupt.assert_called_once_with("cancelled by Xingzi")


@pytest.mark.parametrize("result", [
    None,
    {},
    {"completed": False, "final_response": "partial work"},
    {"interrupted": True, "final_response": "partial work", "interrupt_message": "cancelled"},
])
def test_employee_incomplete_or_interrupted_result_is_not_reported_as_success(result):
    from voidcube.interfaces.cli.application import _background_completion_outcome
    success, _, error = _background_completion_outcome(result)
    assert success is False
    assert error

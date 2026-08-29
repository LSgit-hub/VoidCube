from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from voidcube.systems.supervisor.autonomous_chain_store import AutonomousChainStore
from voidcube.systems.supervisor.autonomous_employee_dispatch_service import (
    AutonomousEmployeeDispatchService,
)
from voidcube.systems.supervisor.autonomous_task_state import AutonomousTaskStateService
from voidcube.systems.supervisor.scheduled_tasks import (
    ScheduledRunLeaseExpiredError,
    ScheduledTaskRuntimeMixin,
    ScheduledTaskStore,
)
from voidcube.systems.supervisor.task_profile_policy import TaskProfilePolicy


def _task(store: ScheduledTaskStore, now: datetime, **overrides):
    payload = {
        "title": "employee work",
        "instruction": "do the work",
        "schedule_type": "once",
        "run_at": now.isoformat(),
        "created_by": "api_b",
        "requested_via": "autonomous_worker",
        "worker_role": "research",
    }
    payload.update(overrides)
    return store.create(payload, now=now)


def test_claim_can_exclude_auto_work_without_blocking_assist_work(tmp_path):
    now = datetime(2026, 8, 21, tzinfo=timezone.utc)
    store = ScheduledTaskStore(tmp_path / "scheduled.db")
    _task(store, now, autonomous_task_id="auto-1")
    assist = _task(
        store,
        now,
        title="assist work",
        requested_via="companion_delegate",
        autonomous_task_id="canonical-assist-1",
    )

    claim = store.claim_due(
        owner_session_id="cli-session",
        now=now,
        exclude_autonomous_work=True,
    )

    assert claim is not None
    assert claim["task"]["schedule_id"] == assist["schedule_id"]


def test_api_a_schedule_snapshot_excludes_api_b_and_employee_work(tmp_path):
    now = datetime(2026, 8, 21, tzinfo=timezone.utc)
    store = ScheduledTaskStore(tmp_path / "scheduled.db")
    api_a = _task(
        store,
        now,
        created_by="api_a",
        requested_via="cli",
        title="用户定时任务",
    )
    _task(store, now, title="员工派工")
    _task(
        store,
        now,
        created_by="api_b",
        requested_via="companion_delegate",
        title="辅助模式定时任务",
    )

    class Host(ScheduledTaskRuntimeMixin):
        _scheduled_task_store = store

    snapshot = Host()._scheduled_task_snapshot(
        include_completed=True,
        scope="api_a_user",
    )

    assert [task["schedule_id"] for task in snapshot["tasks"]] == [
        api_a["schedule_id"]
    ]


def test_companion_execution_context_reports_waiting_executor():
    from voidcube.systems.supervisor.service_runtime import ServiceRuntimeMixin

    class Host(ServiceRuntimeMixin):
        _scheduled_task_store = SimpleNamespace(
            list=lambda include_completed=True: [
                {
                    "schedule_id": "employee-1",
                    "title": "assist work",
                    "worker_role": "general",
                    "created_by": "api_b",
                    "requested_via": "companion_delegate",
                    "status": "active",
                    "active_run_id": None,
                    "created_at": "2026-08-21T00:00:00+00:00",
                }
            ],
            recent_runs=lambda limit=200: [],
        )

    snapshot = Host()._companion_worker_execution_context()

    assert snapshot["employee_executor"]["status"] == "waiting_for_employee_executor"
    assert snapshot["employee_executor"]["queued_count"] == 1


def test_assist_delegate_creates_canonical_task_link():
    from voidcube.systems.supervisor.service_runtime import ServiceRuntimeMixin

    canonical = SimpleNamespace(task_id="canonical-1", status="approved")
    state = SimpleNamespace(
        create_task=lambda **kwargs: canonical,
        update_status=lambda *args, **kwargs: canonical,
        update_metadata=lambda *args, **kwargs: canonical,
    )
    scheduled = SimpleNamespace(create=Mock())
    scheduled.create.side_effect = lambda payload: {
        **payload,
        "schedule_id": "employee-1",
        "created_at": "2026-08-21T00:00:00+00:00",
        "worker_role": payload["worker_role"],
    }

    class Host(ServiceRuntimeMixin):
        _autonomous_task_state = state
        _scheduled_task_store = scheduled
        _resolve_companion_worker_role = staticmethod(lambda role: role or "general")

    result = Host()._create_immediate_companion_execution(
        title="Assist request",
        instruction="Inspect the requested data.",
        requested_via="companion_delegate",
        worker_role="general",
    )

    assert result["autonomous_task_id"] == "canonical-1"
    assert result["canonical_status"] == "approved"
    payload = scheduled.create.call_args.args[0]
    assert payload["autonomous_task_id"] == "canonical-1"


@pytest.mark.asyncio
async def test_assist_api_b_to_employee_finish_and_reconcile(tmp_path):
    chain_store = AutonomousChainStore(tmp_path / "chain.json")
    scheduled_store = ScheduledTaskStore(tmp_path / "scheduled.db")
    governance = Mock()
    task_state = AutonomousTaskStateService(
        store=chain_store,
        governance_repository=governance,
    )

    class Host(ScheduledTaskRuntimeMixin):
        _autonomous_task_state = task_state
        _scheduled_task_store = scheduled_store
        _service_runtime = SimpleNamespace(autonomous_chain_gate_active=False)
        _provider_pool_service = SimpleNamespace(
            dispatch_policy=lambda: {
                "max_concurrent": 1,
                "role_limits": {},
                "role_providers": {},
                "provider_limits": {},
            }
        )

        def _resolve_companion_worker_role(self, role):
            return str(role or "general")

    host = Host()

    # The helper is defined on ServiceRuntimeMixin; compose it for this test
    # without constructing the full Supervisor application.
    from voidcube.systems.supervisor.service_runtime import ServiceRuntimeMixin

    class RuntimeHost(ServiceRuntimeMixin, Host):
        _autonomous_task_state = task_state
        _scheduled_task_store = scheduled_store

        def _resolve_companion_worker_role(self, role):
            return str(role or "general")

    runtime_host = RuntimeHost()
    canonical = runtime_host._create_assist_canonical_task(
        title="Assist E2E",
        instruction="Inspect and report.",
        requested_via="companion_delegate",
        worker_role="general",
    )
    scheduled_store.create(
        {
            "title": canonical.title,
            "instruction": canonical.summary,
            "schedule_type": "once",
            "run_at": "2026-08-21T00:00:00+00:00",
            "created_by": "api_b",
            "requested_via": "companion_delegate",
            "worker_role": "general",
            "autonomous_task_id": canonical.task_id,
        }
    )
    claim = await host.claim_scheduled_task(
        {"owner_session_id": "assist-cli", "lease_seconds": 300}
    )
    assert claim["claim"]["autonomous_task"]["task_id"] == canonical.task_id

    scheduled_store.finish_run(
        claim["claim"]["run"]["run_id"],
        owner_session_id="assist-cli",
        success=True,
        result_summary="Employee completed the inspection.",
    )
    dispatch = AutonomousEmployeeDispatchService(
        task_state=task_state,
        task_store=chain_store,
        scheduled_task_store=scheduled_store,
        task_profile_policy=TaskProfilePolicy(),
        resolve_worker_role=lambda role: role,
        touch_gateway_activity=AsyncMock(),
        record_ui_activity=Mock(),
    )
    updates = await dispatch.reconcile()

    assert updates == [{"task_id": canonical.task_id, "status": "completed"}]
    assert chain_store.get_task(canonical.task_id).status == "completed"


@pytest.mark.asyncio
async def test_employee_result_recovery_routes_assist_result_to_user_without_mem():
    from voidcube.systems.supervisor.service_runtime import (
        ServiceRuntimeMixin,
        ServiceRuntimeState,
        StellarMode,
    )

    task = SimpleNamespace(
        task_id="assist-task",
        title="辅助任务",
        source="companion",
        status="completed",
        metadata={"assist_mode": True},
    )
    task_state = SimpleNamespace(
        update_metadata=Mock(side_effect=lambda task_id, metadata: task)
    )

    class Host(ServiceRuntimeMixin):
        _service_runtime = ServiceRuntimeState(stellar_mode=StellarMode.DAILY_COMPANION)
        _autonomous_task_state = task_state
        _autonomous_task_memory_promotion_service = SimpleNamespace(
            propose=AsyncMock()
        )

    host = Host()
    result = await host._handle_employee_result(
        task,
        {"employee_run_id": "run-1", "result_summary": "已完成"},
        "completed",
    )

    assert result["status"] == "awaiting_user_report"
    assert host._service_runtime.pending_proactive_reminder["task_id"] == "assist-task"
    host._autonomous_task_memory_promotion_service.propose.assert_not_awaited()


@pytest.mark.asyncio
async def test_employee_result_recovery_routes_auto_result_through_mem_review():
    from voidcube.systems.supervisor.service_runtime import (
        ServiceRuntimeMixin,
        ServiceRuntimeState,
        StellarMode,
    )

    task = SimpleNamespace(
        task_id="auto-task",
        title="自治任务",
        source="self_learning",
        status="completed",
        metadata={},
    )
    task_state = SimpleNamespace(
        update_metadata=Mock(side_effect=lambda task_id, metadata: task)
    )
    promotion = AsyncMock(
        return_value={"status": "recorded_only", "source_memory_id": "mem-1"}
    )

    class Host(ServiceRuntimeMixin):
        _service_runtime = ServiceRuntimeState(stellar_mode=StellarMode.AUTO_EVOLUTION)
        _autonomous_task_state = task_state
        _autonomous_task_memory_promotion_service = SimpleNamespace(
            propose=promotion
        )

    result = await Host()._handle_employee_result(
        task,
        {"employee_run_id": "run-1", "result_summary": "已完成"},
        "completed",
    )

    assert result["status"] == "written_to_mem"
    promotion.assert_awaited_once_with(task)


@pytest.mark.asyncio
async def test_recurring_assist_schedule_rotates_canonical_task(tmp_path):
    chain_store = AutonomousChainStore(tmp_path / "chain.json")
    scheduled_store = ScheduledTaskStore(tmp_path / "scheduled.db")
    task_state = AutonomousTaskStateService(
        store=chain_store,
        governance_repository=Mock(),
    )
    canonical = task_state.create_task(
        title="Recurring Assist",
        summary="Run the recurring check.",
        task_type="user",
        source="companion",
        metadata={
            "governance_task_type": "user",
            "task_family": "user",
            "assist_mode": True,
        },
    )
    task_state.update_status(
        canonical.task_id,
        status="approved",
        actor="api_b",
        reason="approved",
    )
    scheduled = scheduled_store.create(
        {
            "title": canonical.title,
            "instruction": canonical.summary,
            "schedule_type": "daily",
            "time_of_day": "13:00",
            "created_by": "api_b",
            "requested_via": "companion_voice",
            "worker_role": "general",
            "autonomous_task_id": canonical.task_id,
        },
        now=datetime(2026, 8, 22, 4, tzinfo=timezone.utc),
    )
    claim = scheduled_store.claim_due(
        owner_session_id="assist-cli",
        now=datetime(2026, 8, 22, 5, tzinfo=timezone.utc),
    )
    assert claim is not None
    task_state.claim_execution(
        canonical.task_id,
        owner_session_id="assist-cli",
    )
    scheduled_store.finish_run(
        claim["run"]["run_id"],
        owner_session_id="assist-cli",
        success=True,
        result_summary="Recurring check complete.",
        now=datetime(2026, 8, 22, 5, tzinfo=timezone.utc),
    )
    dispatch = AutonomousEmployeeDispatchService(
        task_state=task_state,
        task_store=chain_store,
        scheduled_task_store=scheduled_store,
        task_profile_policy=TaskProfilePolicy(),
        resolve_worker_role=lambda role: role,
        touch_gateway_activity=AsyncMock(),
        record_ui_activity=Mock(),
    )
    await dispatch.reconcile()

    rotated = scheduled_store.get(scheduled["schedule_id"])
    assert rotated["autonomous_task_id"] != canonical.task_id
    assert chain_store.get_task(rotated["autonomous_task_id"]).status == "approved"


def test_finish_rejects_a_writeback_after_the_scheduled_lease_expires(tmp_path):
    now = datetime(2026, 8, 21, tzinfo=timezone.utc)
    store = ScheduledTaskStore(tmp_path / "scheduled.db")
    _task(store, now)
    claim = store.claim_due(
        owner_session_id="cli-session",
        now=now,
        lease_seconds=60,
    )
    assert claim is not None

    with pytest.raises(ScheduledRunLeaseExpiredError):
        store.finish_run(
            claim["run"]["run_id"],
            owner_session_id="cli-session",
            success=True,
            result_summary="late result",
            now=now + timedelta(seconds=61),
        )

    run = store.recent_runs(limit=1)[0]
    assert run["status"] == "failed"
    assert run["error"] == "execution lease expired before writeback"


@pytest.mark.asyncio
async def test_supervisor_claim_attaches_the_canonical_autonomous_lease(tmp_path):
    now = datetime(2026, 8, 21, tzinfo=timezone.utc)
    store = ScheduledTaskStore(tmp_path / "scheduled.db")
    task = _task(store, now, autonomous_task_id="auto-1")

    claimed_task = {
        "task_id": "auto-1",
        "status": "running",
        "execution_lease": {
            "generation": 1,
            "attempt_id": "attempt-1",
            "owner_session_id": "cli-session",
            "state": "active",
        },
    }

    class Host(ScheduledTaskRuntimeMixin):
        _scheduled_task_store = store
        _service_runtime = SimpleNamespace(autonomous_chain_gate_active=True)
        _provider_pool_service = SimpleNamespace(
            dispatch_policy=lambda: {
                "max_concurrent": 1,
                "role_limits": {},
                "role_providers": {},
                "provider_limits": {},
            }
        )
        _autonomous_task_state = SimpleNamespace(
            claim_execution=lambda *args, **kwargs: SimpleNamespace(
                model_dump=lambda mode=None: claimed_task
            )
        )

    result = await Host().claim_scheduled_task(
        {
            "owner_session_id": "cli-session",
            "lease_seconds": 300,
        }
    )

    assert result["claim"]["task"]["schedule_id"] == task["schedule_id"]
    assert result["claim"]["autonomous_task"]["execution_lease"]["attempt_id"] == "attempt-1"

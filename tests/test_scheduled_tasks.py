from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest
from fastapi.testclient import TestClient

from systems.supervisor.config_models import (
    SupervisorBodyRuntimeConfig,
    SupervisorConfig,
    SupervisorExecutionConfig,
    SupervisorServiceRuntimeConfig,
)
from systems.supervisor.supervisor import Supervisor
from systems.supervisor.scheduled_tasks import ScheduledTaskStore
from VoidCube_cli.scheduled_executor import ScheduledTaskExecutorRuntime, ScheduledWritebackOutbox


def test_once_schedule_claim_and_api_a_writeback(tmp_path) -> None:
    store = ScheduledTaskStore(tmp_path / "scheduled_tasks.db")
    now = datetime(2026, 7, 28, 1, 0, tzinfo=timezone.utc)
    task = store.create(
        {
            "title": "整理项目进展",
            "instruction": "读取项目状态并整理今天的进展。",
            "schedule_type": "once",
            "run_at": "2026-07-28T00:59:00+00:00",
            "created_by": "api_b",
            "requested_via": "voice",
        },
        now=now,
    )

    claim = store.claim_due(owner_session_id="cli-main", now=now)
    assert claim is not None
    assert claim["task"]["schedule_id"] == task["schedule_id"]
    assert claim["run"]["owner_session_id"] == "cli-main"
    assert store.claim_due(owner_session_id="cli-other", now=now) is None

    result = store.finish_run(
        claim["run"]["run_id"],
        owner_session_id="cli-main",
        success=True,
        result_summary="已完成整理",
        now=now,
    )
    assert result["task"]["status"] == "completed"
    assert result["task"]["next_run_at"] is None
    assert result["run"]["status"] == "completed"
    with pytest.raises(ValueError, match="must be updated"):
        store.set_status(task["schedule_id"], "active")


def test_daily_schedule_advances_after_failed_api_a_run(tmp_path) -> None:
    store = ScheduledTaskStore(tmp_path / "scheduled_tasks.db")
    created_at = datetime(2026, 7, 27, 10, 59, tzinfo=timezone.utc)
    task = store.create(
        {
            "title": "每日汇总",
            "instruction": "生成每日汇总。",
            "schedule_type": "daily",
            "time_of_day": "19:00",
            "timezone": "Asia/Shanghai",
        },
        now=created_at,
    )
    assert task["next_run_at"] == "2026-07-27T11:00:00+00:00"

    due_at = datetime(2026, 7, 27, 11, 0, tzinfo=timezone.utc)
    claim = store.claim_due(owner_session_id="cli-main", now=due_at)
    assert claim is not None
    result = store.finish_run(
        claim["run"]["run_id"],
        owner_session_id="cli-main",
        success=False,
        error="agent unavailable",
        now=due_at,
    )
    assert result["task"]["status"] == "active"
    assert result["task"]["next_run_at"] == "2026-07-28T11:00:00+00:00"
    assert result["task"]["last_run_status"] == "failed"


def test_pause_update_resume_and_delete_schedule(tmp_path) -> None:
    store = ScheduledTaskStore(tmp_path / "scheduled_tasks.db")
    task = store.create(
        {
            "title": "原任务",
            "instruction": "原指令",
            "schedule_type": "once",
            "run_at": "2026-07-30T08:00:00+08:00",
        }
    )
    paused = store.set_status(task["schedule_id"], "paused")
    assert paused["status"] == "paused"
    updated = store.update(
        task["schedule_id"],
        {"title": "新任务", "run_at": "2026-07-30T09:00:00+08:00"},
    )
    assert updated["title"] == "新任务"
    assert updated["status"] == "paused"
    assert store.set_status(task["schedule_id"], "active")["status"] == "active"
    assert store.delete(task["schedule_id"])["schedule_id"] == task["schedule_id"]
    with pytest.raises(KeyError):
        store.get(task["schedule_id"])


def test_running_schedule_rejects_mutation_and_foreign_writeback(tmp_path) -> None:
    store = ScheduledTaskStore(tmp_path / "scheduled_tasks.db")
    now = datetime(2026, 7, 28, 1, 0, tzinfo=timezone.utc)
    task = store.create(
        {
            "title": "执行中的任务",
            "instruction": "执行。",
            "run_at": "2026-07-28T00:00:00+00:00",
        },
        now=now,
    )
    claim = store.claim_due(owner_session_id="cli-main", now=now)
    assert claim is not None
    with pytest.raises(ValueError, match="running schedule"):
        store.delete(task["schedule_id"])
    with pytest.raises(ValueError, match="another CLI session"):
        store.finish_run(
            claim["run"]["run_id"],
            owner_session_id="cli-other",
            success=True,
            now=now,
        )


def _make_supervisor(tmp_path) -> Supervisor:
    return Supervisor(
        SupervisorConfig(
            execution=SupervisorExecutionConfig(git_repo_path=str(tmp_path)),
            soul_store_path=str(tmp_path / "supervisor-runtime"),
            scheduled_task_store_path=str(tmp_path / "scheduled-tasks.db"),
            body_runtime=SupervisorBodyRuntimeConfig(state_root=str(tmp_path / "body-state")),
            service_runtime=SupervisorServiceRuntimeConfig(
                governor_llm_advisory_enabled=False,
                endogenous_drive_lm_task_generation_enabled=False,
            ),
        )
    )


def test_supervisor_scheduled_task_routes_keep_management_separate_from_claim(tmp_path) -> None:
    supervisor = _make_supervisor(tmp_path)
    client = TestClient(supervisor.app)
    created = client.post(
        "/scheduled-tasks",
        json={
            "title": "CLI 创建的计划",
            "instruction": "由 API-A 完成",
            "schedule_type": "once",
            "run_at": "2026-07-28T00:00:00+00:00",
            "created_by": "api_a",
        },
    )
    assert created.status_code == 200
    task = created.json()["task"]
    assert client.get("/scheduled-tasks").json()["count"] == 1
    assert client.post(f"/scheduled-tasks/{task['schedule_id']}/pause").json()["status"] == "paused"
    assert client.post(f"/scheduled-tasks/{task['schedule_id']}/resume").json()["status"] == "active"

    claimed = client.post(
        "/scheduled-tasks/claim",
        json={"owner_session_id": "main-cli"},
    )
    assert claimed.status_code == 200
    run = claimed.json()["claim"]["run"]
    renewed = client.post(
        f"/scheduled-task-runs/{run['run_id']}/renew",
        json={"owner_session_id": "main-cli", "lease_seconds": 300},
    )
    assert renewed.status_code == 200
    assert renewed.json()["status"] == "renewed"
    finished = client.post(
        f"/scheduled-task-runs/{run['run_id']}/finish",
        json={
            "owner_session_id": "main-cli",
            "success": True,
            "result_summary": "完成",
        },
    )
    assert finished.status_code == 200
    assert finished.json()["task"]["status"] == "completed"


@pytest.mark.asyncio
async def test_daily_companion_can_manage_but_not_execute_schedule(tmp_path) -> None:
    supervisor = _make_supervisor(tmp_path)
    supervisor._recall_companion_context = AsyncMock(return_value="")  # type: ignore[method-assign]
    supervisor._persist_companion_turn_pair = AsyncMock(return_value=True)  # type: ignore[method-assign]
    supervisor._call_companion_model = AsyncMock(  # type: ignore[method-assign]
        return_value={
            "reply_text": "已经安排好了。",
            "reason": "explicit_user_schedule_request",
            "schedule_action": {
                "action": "create",
                "task": {
                    "title": "晚间整理",
                    "instruction": "整理今日项目进展",
                    "schedule_type": "once",
                    "run_at": "2026-07-29T20:00:00+08:00",
                },
            },
        }
    )

    result = await supervisor.handle_companion_message(text="明晚八点整理项目进展")
    assert result["status"] == "ok"
    assert result["schedule_action_result"]["ok"] is True
    task = supervisor._scheduled_task_store.list()[0]
    assert task["created_by"] == "api_b"
    assert task["requested_via"] == "companion_voice"
    assert task["active_run_id"] is None
    schedule_context = supervisor._call_companion_model.call_args.kwargs["payload"]["scheduled_tasks"]
    assert schedule_context == {"count": 0, "omitted_count": 0, "items": []}


def test_main_cli_scheduled_executor_starts_api_a_background_and_writes_back(tmp_path) -> None:
    callbacks = []
    host = SimpleNamespace(
        session_id="main-cli",
        _scheduled_execution_active=False,
        _autonomous_component_host=SimpleNamespace(_agent_running=False),
        _is_embedded_autonomous_component=lambda: False,
    )

    def start_background(prompt, **kwargs):
        callbacks.append(kwargs["on_complete"])
        assert "不要把它交给 Auto 自主链" in prompt
        return True

    host._start_background_agent_task = start_background
    runtime = ScheduledTaskExecutorRuntime(
        host,
        poll_interval_seconds=0.5,
        outbox_path=tmp_path / "writebacks.db",
    )
    responses = [
        {
            "status": "claimed",
            "claim": {
                "task": {"title": "计划任务", "instruction": "完成任务"},
                "run": {"run_id": "run-1"},
            },
        },
        {"status": "completed"},
    ]
    runtime._post = Mock(side_effect=responses)  # type: ignore[method-assign]

    runtime.poll_workflow()
    assert host._scheduled_execution_active is True
    callbacks[0](True, "任务结果", "")
    assert host._scheduled_execution_active is False
    assert runtime._post.call_args_list[-1].args[0] == "/scheduled-task-runs/run-1/finish"


def test_main_cli_scheduled_executor_waits_for_running_auto_task(tmp_path) -> None:
    host = SimpleNamespace(
        session_id="main-cli",
        _scheduled_execution_active=False,
        _autonomous_component_host=SimpleNamespace(_agent_running=True),
        _is_embedded_autonomous_component=lambda: False,
        _start_background_agent_task=Mock(),
    )
    runtime = ScheduledTaskExecutorRuntime(
        host,
        poll_interval_seconds=0.5,
        outbox_path=tmp_path / "writebacks.db",
    )
    runtime._post = Mock()  # type: ignore[method-assign]

    runtime.poll_workflow()

    runtime._post.assert_not_called()
    host._start_background_agent_task.assert_not_called()


def test_api_a_schedule_tool_only_calls_management_surface() -> None:
    from tools.scheduled_task_tool import scheduled_task_tool

    with patch("tools.scheduled_task_tool._request_json") as request_json:
        request_json.return_value = {"status": "created"}
        result = scheduled_task_tool(
            action="create",
            title="计划任务",
            instruction="以后执行",
            schedule_type="once",
            run_at="2026-07-29T20:00:00+08:00",
        )
    assert "created" in result
    path, = request_json.call_args.args
    assert path == "/scheduled-tasks"
    assert request_json.call_args.kwargs["payload"]["created_by"] == "api_a"


def test_legacy_json_migrates_once_to_sqlite(tmp_path) -> None:
    legacy = tmp_path / "scheduled_tasks.json"
    database = tmp_path / "scheduled_tasks.db"
    legacy.write_text(
        json.dumps(
            {
                "version": 1,
                "schedules": [
                    {
                        "schedule_id": "legacy-1",
                        "title": "旧计划",
                        "instruction": "保留迁移数据",
                        "schedule_type": "once",
                        "timezone": "Asia/Shanghai",
                        "status": "active",
                        "created_by": "api_b",
                        "requested_via": "companion_voice",
                        "created_at": "2026-07-28T00:00:00+00:00",
                        "updated_at": "2026-07-28T00:00:00+00:00",
                        "next_run_at": "2026-07-29T00:00:00+00:00",
                        "last_run_at": None,
                        "last_run_status": None,
                        "active_run_id": None,
                        "run_at": "2026-07-29T00:00:00+00:00",
                    }
                ],
                "runs": [],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    store = ScheduledTaskStore(database, legacy_json_path=legacy)

    assert database.read_bytes().startswith(b"SQLite format 3")
    assert store.get("legacy-1")["title"] == "旧计划"
    assert not legacy.exists()
    assert (tmp_path / "scheduled_tasks.json.migrated").exists()
    assert ScheduledTaskStore(database, legacy_json_path=legacy).get("legacy-1")["title"] == "旧计划"
    legacy.write_text("{stale", encoding="utf-8")
    assert ScheduledTaskStore(database, legacy_json_path=legacy).get("legacy-1")["title"] == "旧计划"
    assert not legacy.exists()


def test_corrupt_legacy_json_fails_without_overwriting_source(tmp_path) -> None:
    legacy = tmp_path / "scheduled_tasks.json"
    legacy.write_text("{broken", encoding="utf-8")

    with pytest.raises(RuntimeError, match="migration source is unreadable"):
        ScheduledTaskStore(tmp_path / "scheduled_tasks.db", legacy_json_path=legacy)

    assert legacy.read_text(encoding="utf-8") == "{broken"


def test_lease_renewal_prevents_reclaim_and_finish_is_idempotent(tmp_path) -> None:
    from datetime import timedelta

    store = ScheduledTaskStore(tmp_path / "scheduled_tasks.db")
    now = datetime(2026, 7, 28, 1, 0, tzinfo=timezone.utc)
    task = store.create(
        {
            "title": "长任务",
            "instruction": "持续执行",
            "run_at": "2026-07-28T00:00:00+00:00",
        },
        now=now,
    )
    claim = store.claim_due(owner_session_id="cli-main", now=now, lease_seconds=60)
    assert claim is not None
    run_id = claim["run"]["run_id"]
    renewed = store.renew_run(
        run_id,
        owner_session_id="cli-main",
        lease_seconds=60,
        now=now + timedelta(seconds=50),
    )
    assert renewed["run"]["lease_expires_at"] == "2026-07-28T01:01:50+00:00"
    assert store.claim_due(
        owner_session_id="cli-other",
        now=now + timedelta(seconds=61),
    ) is None

    first = store.finish_run(
        run_id,
        owner_session_id="cli-main",
        success=True,
        result_summary="完成",
        now=now + timedelta(seconds=70),
    )
    repeated = store.finish_run(
        run_id,
        owner_session_id="cli-main",
        success=True,
        result_summary="完成",
        now=now + timedelta(seconds=71),
    )
    assert first["task"]["schedule_id"] == task["schedule_id"]
    assert repeated["run"]["status"] == "completed"


def test_failed_once_schedule_is_visible_as_failed_terminal_state(tmp_path) -> None:
    store = ScheduledTaskStore(tmp_path / "scheduled_tasks.db")
    now = datetime(2026, 7, 28, 1, 0, tzinfo=timezone.utc)
    task = store.create(
        {"title": "失败任务", "instruction": "执行", "run_at": "2026-07-28T00:00:00+00:00"},
        now=now,
    )
    claim = store.claim_due(owner_session_id="cli-main", now=now)
    result = store.finish_run(
        claim["run"]["run_id"],
        owner_session_id="cli-main",
        success=False,
        error="agent failed",
        now=now,
    )
    assert result["task"]["status"] == "failed"
    assert result["task"]["next_run_at"] is None
    with pytest.raises(ValueError, match="must be updated"):
        store.set_status(task["schedule_id"], "active")


def test_scheduled_writeback_outbox_survives_reopen(tmp_path) -> None:
    path = tmp_path / "writebacks.db"
    ScheduledWritebackOutbox(path).enqueue(
        "run-1",
        {"owner_session_id": "cli-main", "success": True, "result_summary": "完成", "error": ""},
    )

    reopened = ScheduledWritebackOutbox(path)
    assert reopened.pending_count() == 1
    assert reopened.next_due()["_outbox_run_id"] == "run-1"
    reopened.mark_delivered("run-1")
    assert reopened.pending_count() == 0


def test_scheduled_executor_waits_for_foreground_api_a_and_execution_gate(tmp_path) -> None:
    host = SimpleNamespace(
        session_id="main-cli",
        _scheduled_execution_active=False,
        _agent_running=True,
        _command_running=False,
        _background_tasks={},
        _api_a_execution_gate=threading.Lock(),
        _autonomous_component_host=SimpleNamespace(_agent_running=False),
        _is_embedded_autonomous_component=lambda: False,
        _start_background_agent_task=Mock(),
    )
    runtime = ScheduledTaskExecutorRuntime(host, outbox_path=tmp_path / "writebacks.db")
    runtime._post = Mock()  # type: ignore[method-assign]

    runtime.poll_workflow()

    runtime._post.assert_not_called()
    host._start_background_agent_task.assert_not_called()


def test_scheduled_executor_waits_for_manual_background_api_a(tmp_path) -> None:
    background_thread = Mock()
    background_thread.is_alive.return_value = True
    host = SimpleNamespace(
        session_id="main-cli",
        _scheduled_execution_active=False,
        _agent_running=False,
        _command_running=False,
        _background_tasks={"manual-background": background_thread},
        _api_a_execution_gate=threading.Lock(),
        _autonomous_component_host=SimpleNamespace(_agent_running=False),
        _is_embedded_autonomous_component=lambda: False,
        _start_background_agent_task=Mock(),
    )
    runtime = ScheduledTaskExecutorRuntime(host, outbox_path=tmp_path / "writebacks.db")
    runtime._post = Mock()  # type: ignore[method-assign]

    runtime.poll_workflow()

    runtime._post.assert_not_called()
    host._start_background_agent_task.assert_not_called()


def test_scheduled_executor_holds_api_a_gate_until_writeback(tmp_path) -> None:
    callbacks = []
    gate = threading.Lock()
    host = SimpleNamespace(
        session_id="main-cli",
        _scheduled_execution_active=False,
        _agent_running=False,
        _command_running=False,
        _background_tasks={},
        _api_a_execution_gate=gate,
        _autonomous_component_host=SimpleNamespace(_agent_running=False),
        _is_embedded_autonomous_component=lambda: False,
    )

    def start_background(_prompt, **kwargs):
        callbacks.append(kwargs["on_complete"])
        return True

    host._start_background_agent_task = start_background
    runtime = ScheduledTaskExecutorRuntime(host, outbox_path=tmp_path / "writebacks.db")
    runtime._post = Mock(
        side_effect=[
            {"claim": {"task": {"title": "计划", "instruction": "执行"}, "run": {"run_id": "run-1"}}},
            {"status": "completed"},
        ]
    )  # type: ignore[method-assign]

    runtime.poll_workflow()
    assert gate.locked()
    callbacks[0](True, "完成", "")
    assert not gate.locked()
    assert runtime._outbox.pending_count() == 0


def test_background_completion_outcome_treats_agent_error_as_failure() -> None:
    from cli import _background_completion_outcome

    success, response, error = _background_completion_outcome({"error": "provider failed"})

    assert success is False
    assert response == "Error: provider failed"
    assert error == "provider failed"

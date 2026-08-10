from __future__ import annotations

import json
import queue
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
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
from VoidCube_cli.scheduled_executor import (
    ScheduledTaskExecutorPorts,
    ScheduledTaskExecutorRuntime,
    ScheduledWritebackOutbox,
)
from VoidCube_cli.background_task_runtime import BackgroundTaskState
from VoidCube_cli.input_process_loop import run_input_process_loop


def _executor_ports(host: SimpleNamespace) -> ScheduledTaskExecutorPorts:
    def auto_task_running() -> bool:
        component = getattr(host, "_autonomous_execution_host", None)
        return bool(component is not None and getattr(component, "_agent_running", False))

    return ScheduledTaskExecutorPorts(
        auto_task_running=auto_task_running,
        execution_gate=getattr(host, "_scheduled_execution_gate", None),
        get_session_id=lambda: str(getattr(host, "session_id", "") or ""),
        set_execution_active=lambda active: setattr(
            host, "_scheduled_execution_active", bool(active)
        ),
        start_background_task=getattr(
            host,
            "_start_scheduled_execution_task",
            host._start_background_agent_task,
        ),
    )


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
        execution_provider="research-endpoint",
        execution_model="research-model",
        elapsed_ms=1250,
        now=now,
    )
    assert result["task"]["status"] == "completed"
    assert result["task"]["next_run_at"] is None
    assert result["run"]["status"] == "completed"
    assert result["run"]["execution_provider"] == "research-endpoint"
    assert result["run"]["execution_model"] == "research-model"
    assert result["run"]["elapsed_ms"] == 1250
    with pytest.raises(ValueError, match="must be updated"):
        store.set_status(task["schedule_id"], "active")


def test_dispatch_skips_saturated_role_and_reports_provider_occupancy(tmp_path) -> None:
    store = ScheduledTaskStore(tmp_path / "scheduled_tasks.db")
    now = datetime(2026, 7, 28, 1, 0, tzinfo=timezone.utc)
    requests = [
        ("调研一", "research", "2026-07-28T00:56:00+00:00"),
        ("调研二", "research", "2026-07-28T00:57:00+00:00"),
        ("工程一", "coding", "2026-07-28T00:58:00+00:00"),
    ]
    for title, role, run_at in requests:
        store.create(
            {
                "title": title,
                "instruction": title,
                "schedule_type": "once",
                "run_at": run_at,
                "created_by": "api_b",
                "worker_role": role,
            },
            now=now,
        )

    policy = {
        "max_concurrent": 2,
        "role_limits": {"research": 1, "coding": 1},
        "role_providers": {"research": "provider-r", "coding": "provider-c"},
    }
    first = store.claim_due(owner_session_id="cli-main", now=now, **policy)
    second = store.claim_due(owner_session_id="cli-main", now=now, **policy)
    blocked = store.claim_due(owner_session_id="cli-main", now=now, **policy)

    assert first is not None and first["task"]["title"] == "调研一"
    assert first["run"]["execution_provider"] == "provider-r"
    assert second is not None and second["task"]["title"] == "工程一"
    assert second["run"]["execution_provider"] == "provider-c"
    assert blocked is None

    state = store.dispatch_state(now=now, **policy)
    assert state["active_count"] == 2
    assert state["queued_count"] == 1
    assert {item["role"]: item for item in state["roles"]}["research"] == {
        "role": "research",
        "active": 1,
        "queued": 1,
        "limit": 1,
    }
    assert {item["provider"]: item for item in state["providers"]} == {
        "provider-r": {
            "provider": "provider-r", "active": 1, "queued": 1, "limit": 2,
            "cooldown_until": "", "cooldown_remaining_seconds": 0,
            "failure_count": 0, "last_status": None,
            "metrics": {
                "sample_size": 0, "success_count": 0,
                "success_rate_percent": None, "average_elapsed_ms": None,
                "rate_limit_count": 0, "last_completed_at": "",
            },
        },
        "provider-c": {
            "provider": "provider-c", "active": 1, "queued": 0, "limit": 2,
            "cooldown_until": "", "cooldown_remaining_seconds": 0,
            "failure_count": 0, "last_status": None,
            "metrics": {
                "sample_size": 0, "success_count": 0,
                "success_rate_percent": None, "average_elapsed_ms": None,
                "rate_limit_count": 0, "last_completed_at": "",
            },
        },
    }

    store.finish_run(
        first["run"]["run_id"],
        owner_session_id="cli-main",
        success=True,
        now=now,
    )
    third = store.claim_due(owner_session_id="cli-main", now=now, **policy)
    assert third is not None and third["task"]["title"] == "调研二"


def test_dispatch_skips_saturated_provider_without_blocking_other_provider(tmp_path) -> None:
    store = ScheduledTaskStore(tmp_path / "scheduled_tasks.db")
    now = datetime(2026, 7, 28, 1, 0, tzinfo=timezone.utc)
    for title, role in (("调研", "research"), ("工程", "coding"), ("媒体", "media")):
        store.create(
            {
                "title": title,
                "instruction": title,
                "schedule_type": "once",
                "run_at": "2026-07-28T00:59:00+00:00",
                "created_by": "api_b",
                "worker_role": role,
            },
            now=now,
        )
    policy = {
        "max_concurrent": 3,
        "role_limits": {"research": 1, "coding": 1, "media": 1},
        "role_providers": {
            "research": "shared-provider",
            "coding": "shared-provider",
            "media": "media-provider",
        },
        "provider_limits": {"shared-provider": 1, "media-provider": 1},
    }

    first = store.claim_due(owner_session_id="cli-main", now=now, **policy)
    second = store.claim_due(owner_session_id="cli-main", now=now, **policy)

    assert first is not None and first["task"]["title"] == "调研"
    assert second is not None and second["task"]["title"] == "媒体"
    assert store.claim_due(owner_session_id="cli-main", now=now, **policy) is None


def test_provider_429_cooldown_uses_retry_after_and_success_clears_state(tmp_path) -> None:
    store = ScheduledTaskStore(tmp_path / "scheduled_tasks.db")
    now = datetime(2026, 7, 28, 1, 0, tzinfo=timezone.utc)
    for index in range(3):
        store.create(
            {
                "title": f"任务 {index}",
                "instruction": "执行",
                "schedule_type": "once",
                "run_at": "2026-07-28T00:59:00+00:00",
                "created_by": "api_b",
                "worker_role": "research",
            },
            now=now,
        )
    policy = {
        "max_concurrent": 2,
        "role_limits": {"research": 1},
        "role_providers": {"research": "limited-provider"},
        "provider_limits": {"limited-provider": 2},
    }

    first = store.claim_due(owner_session_id="cli-main", now=now, **policy)
    assert first is not None
    store.finish_run(
        first["run"]["run_id"],
        owner_session_id="cli-main",
        success=False,
        error="HTTP 429",
        rate_limited=True,
        retry_after_seconds=75,
        error_code=429,
        elapsed_ms=1000,
        now=now,
    )
    state = store.dispatch_state(now=now, **policy)
    provider = state["providers"][0]
    assert provider["cooldown_remaining_seconds"] == 75
    assert provider["failure_count"] == 1
    assert provider["last_status"] == 429
    assert provider["metrics"] == {
        "sample_size": 1,
        "success_count": 0,
        "success_rate_percent": 0.0,
        "average_elapsed_ms": 1000,
        "rate_limit_count": 1,
        "last_completed_at": now.isoformat(),
    }
    assert store.claim_due(owner_session_id="cli-main", now=now, **policy) is None

    after_first_cooldown = now.replace(minute=1, second=16)
    second = store.claim_due(
        owner_session_id="cli-main", now=after_first_cooldown, **policy
    )
    assert second is not None
    store.finish_run(
        second["run"]["run_id"],
        owner_session_id="cli-main",
        success=False,
        rate_limited=True,
        elapsed_ms=3000,
        now=after_first_cooldown,
    )
    state = store.dispatch_state(now=after_first_cooldown, **policy)
    provider = state["providers"][0]
    assert provider["cooldown_remaining_seconds"] == 60
    assert provider["failure_count"] == 2

    after_second_cooldown = now.replace(minute=2, second=17)
    third = store.claim_due(
        owner_session_id="cli-main", now=after_second_cooldown, **policy
    )
    assert third is not None
    store.finish_run(
        third["run"]["run_id"],
        owner_session_id="cli-main",
        success=True,
        elapsed_ms=2000,
        now=after_second_cooldown,
    )
    provider = store.dispatch_state(now=after_second_cooldown, **policy)["providers"][0]
    assert provider["cooldown_remaining_seconds"] == 0
    assert provider["failure_count"] == 0
    assert provider["last_status"] is None
    assert provider["metrics"] == {
        "sample_size": 3,
        "success_count": 1,
        "success_rate_percent": 33.3,
        "average_elapsed_ms": 2000,
        "rate_limit_count": 2,
        "last_completed_at": after_second_cooldown.isoformat(),
    }


def test_manual_provider_cooldown_reset_releases_queued_work(tmp_path) -> None:
    store = ScheduledTaskStore(tmp_path / "scheduled_tasks.db")
    now = datetime(2026, 7, 28, 1, 0, tzinfo=timezone.utc)
    for index in range(2):
        store.create(
            {
                "title": f"任务 {index}",
                "instruction": "执行",
                "schedule_type": "once",
                "run_at": "2026-07-28T00:59:00+00:00",
                "created_by": "api_b",
                "worker_role": "research",
            },
            now=now,
        )
    policy = {
        "role_providers": {"research": "limited-provider"},
        "provider_limits": {"limited-provider": 2},
    }
    first = store.claim_due(owner_session_id="cli-main", now=now, **policy)
    assert first is not None
    store.finish_run(
        first["run"]["run_id"],
        owner_session_id="cli-main",
        success=False,
        rate_limited=True,
        now=now,
    )

    assert store.claim_due(owner_session_id="cli-main", now=now, **policy) is None
    assert store.clear_provider_cooldown("limited-provider") is True
    assert store.clear_provider_cooldown("limited-provider") is False
    assert store.claim_due(owner_session_id="cli-main", now=now, **policy) is not None


def test_provider_metrics_use_latest_fifty_runs(tmp_path) -> None:
    store = ScheduledTaskStore(tmp_path / "scheduled_tasks.db")
    now = datetime(2026, 7, 28, 1, 0, tzinfo=timezone.utc)
    policy = {
        "role_providers": {"research": "metrics-provider"},
        "provider_limits": {"metrics-provider": 2},
    }
    for index in range(55):
        completed_at = now + timedelta(seconds=index)
        store.create(
            {
                "title": f"样本 {index}",
                "instruction": "执行",
                "schedule_type": "once",
                "run_at": now.isoformat(),
                "created_by": "api_b",
                "worker_role": "research",
            },
            now=now,
        )
        claim = store.claim_due(
            owner_session_id="cli-main", now=completed_at, **policy
        )
        assert claim is not None
        store.finish_run(
            claim["run"]["run_id"],
            owner_session_id="cli-main",
            success=index % 2 == 0,
            elapsed_ms=index * 10,
            now=completed_at,
        )

    metrics = store.dispatch_state(now=now + timedelta(minutes=2), **policy)[
        "providers"
    ][0]["metrics"]
    assert metrics == {
        "sample_size": 50,
        "success_count": 25,
        "success_rate_percent": 50.0,
        "average_elapsed_ms": 295,
        "rate_limit_count": 0,
        "last_completed_at": (now + timedelta(seconds=54)).isoformat(),
    }


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
                    "worker_role": "coding",
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
    assert task["worker_role"] == "coding"
    assert task["active_run_id"] is None
    schedule_context = supervisor._call_companion_model.call_args.kwargs["payload"]["scheduled_tasks"]
    assert schedule_context == {"count": 0, "omitted_count": 0, "items": []}


@pytest.mark.asyncio
async def test_daily_companion_create_uses_title_when_instruction_is_omitted(tmp_path) -> None:
    supervisor = _make_supervisor(tmp_path)
    supervisor._recall_companion_context = AsyncMock(return_value="")  # type: ignore[method-assign]
    supervisor._persist_companion_turn_pair = AsyncMock(return_value=True)  # type: ignore[method-assign]
    supervisor._call_companion_model = AsyncMock(  # type: ignore[method-assign]
        return_value={
            "reply_text": "十分钟后提醒你测试。",
            "reason": "explicit_user_schedule_request",
            "schedule_action": {
                "action": "create",
                "task": {
                    "title": "提醒用户进行定时任务测试",
                    "schedule_type": "once",
                    "run_at": "2026-07-29T20:00:00+08:00",
                },
            },
        }
    )

    result = await supervisor.handle_companion_message(text="十分钟后提醒我测试")

    assert result["status"] == "ok"
    assert result["schedule_action_result"]["ok"] is True
    task = supervisor._scheduled_task_store.list()[0]
    assert task["instruction"] == "提醒用户进行定时任务测试"
    prompt = supervisor._call_companion_model.call_args.kwargs["system_prompt"]
    assert "必须包含 title、instruction 和 schedule_type" in prompt


def test_main_cli_scheduled_executor_starts_api_a_background_and_writes_back(tmp_path) -> None:
    callbacks = []
    host = SimpleNamespace(
        session_id="main-cli",
        _scheduled_execution_active=False,
        _background_task_state=BackgroundTaskState(),
        _autonomous_execution_host=SimpleNamespace(_agent_running=False),
    )

    def start_background(prompt, **kwargs):
        callbacks.append(kwargs["on_complete"])
        assert "不要把它交给 Auto 自主链" in prompt
        assert kwargs["request_timeout_seconds"] == 120.0
        assert kwargs["timeout_seconds"] == 600.0
        assert kwargs["persist_session"] is False
        return True

    host._start_background_agent_task = start_background
    runtime = ScheduledTaskExecutorRuntime(
        _executor_ports(host),
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


def test_main_cli_scheduled_executor_keeps_multiple_workers_active(tmp_path) -> None:
    callbacks = []
    host = SimpleNamespace(
        session_id="main-cli",
        _scheduled_execution_active=False,
        _background_task_state=BackgroundTaskState(),
        _autonomous_execution_host=SimpleNamespace(_agent_running=False),
    )

    def start_background(_prompt, **kwargs):
        callbacks.append(kwargs["on_complete"])
        return True

    claims = iter(
        [
            {
                "status": "claimed",
                "claim": {
                    "task": {"title": "调研", "instruction": "查资料", "worker_role": "research"},
                    "run": {"run_id": "run-research"},
                },
            },
            {
                "status": "claimed",
                "claim": {
                    "task": {"title": "工程", "instruction": "改代码", "worker_role": "coding"},
                    "run": {"run_id": "run-coding"},
                },
            },
        ]
    )
    posts = []

    def post(path, payload):
        posts.append((path, payload))
        if path == "/scheduled-tasks/claim":
            return next(claims)
        return {"status": "completed"}

    host._start_background_agent_task = start_background
    runtime = ScheduledTaskExecutorRuntime(
        _executor_ports(host),
        poll_interval_seconds=0.5,
        outbox_path=tmp_path / "concurrent-writebacks.db",
    )
    runtime._post = post  # type: ignore[method-assign]

    runtime.poll_workflow()
    runtime._last_poll_at = 0
    runtime.poll_workflow()

    assert len(callbacks) == 2
    assert host._scheduled_execution_active is True
    assert runtime._active_run_ids == {"run-research", "run-coding"}
    callbacks[0](True, "调研完成", "")
    assert host._scheduled_execution_active is True
    assert runtime._active_run_ids == {"run-coding"}
    callbacks[1](True, "工程完成", "")
    assert host._scheduled_execution_active is False
    assert runtime._active_run_ids == set()
    assert [path for path, _payload in posts].count("/scheduled-tasks/claim") == 2
    assert [path for path, _payload in posts].count(
        "/scheduled-task-runs/run-research/finish"
    ) == 1
    assert [path for path, _payload in posts].count(
        "/scheduled-task-runs/run-coding/finish"
    ) == 1


@pytest.mark.asyncio
async def test_companion_media_request_is_delegated_to_api_a_and_hidden_from_schedule_ui(
    tmp_path,
) -> None:
    supervisor = _make_supervisor(tmp_path)
    supervisor._recall_companion_context = AsyncMock(return_value="")  # type: ignore[method-assign]
    supervisor._persist_companion_turn_pair = AsyncMock(return_value=True)  # type: ignore[method-assign]
    supervisor._call_companion_model = AsyncMock(  # type: ignore[method-assign]
        return_value={
            "reply_text": "我已交给 API-A 查找并播放。",
            "reason": "explicit_media_request",
            "schedule_action": {"action": "none"},
            "media_action": {"action": "delegate", "query": "播放测试视频"},
        }
    )

    result = await supervisor.handle_companion_message(text="帮我播放测试视频")

    assert result["media_action_result"]["ok"] is True
    assert result["disposition"] == "delegate_to_api_a"
    stored = supervisor._scheduled_task_store.list(include_completed=True)
    assert len(stored) == 1
    assert stored[0]["requested_via"] == "companion_media"
    assert "media_play" in stored[0]["instruction"]
    assert supervisor._scheduled_task_snapshot()["tasks"] == []
    assert supervisor._companion_schedule_context()["items"] == []
    prompt = supervisor._call_companion_model.call_args.kwargs["system_prompt"]
    assert "schedule_action.action 必须为 none" in prompt


@pytest.mark.asyncio
async def test_companion_tool_request_is_planned_and_delegated_to_api_a(tmp_path) -> None:
    supervisor = _make_supervisor(tmp_path)
    supervisor._recall_companion_context = AsyncMock(return_value="")  # type: ignore[method-assign]
    supervisor._persist_companion_turn_pair = AsyncMock(return_value=True)  # type: ignore[method-assign]
    supervisor._call_companion_model = AsyncMock(  # type: ignore[method-assign]
        return_value={
            "reply_text": "我已整理计划并交给 API-A。",
            "reason": "requires_repository_tools",
            "schedule_action": {"action": "none"},
            "media_action": {"action": "none"},
            "execution_action": {
                "action": "delegate",
                "title": "检查项目测试",
                "instruction": "检查当前项目并运行相关测试。",
                "worker_role": "coding",
                "plan_steps": ["读取项目配置", "运行相关测试", "汇总失败原因"],
                "skills": ["project-testing"],
                "toolsets": ["terminal"],
            },
        }
    )

    result = await supervisor.handle_companion_message(text="检查这个项目的测试")

    assert result["disposition"] == "delegate_to_api_a"
    assert result["execution_action_result"]["ok"] is True
    assert result["execution_action_result"]["worker_role"] == "coding"
    assert "计划：1. 读取项目配置；2. 运行相关测试" in result["reply_text"]
    stored = supervisor._scheduled_task_store.list(include_completed=True)
    assert len(stored) == 1
    assert stored[0]["created_by"] == "api_b"
    assert stored[0]["requested_via"] == "companion_delegate"
    assert stored[0]["worker_role"] == "coding"
    assert "1. 读取项目配置" in stored[0]["instruction"]
    assert "建议技能：project-testing" in stored[0]["instruction"]
    assert "建议工具集：terminal" in stored[0]["instruction"]
    assert supervisor._scheduled_task_snapshot()["tasks"] == []
    assert supervisor._companion_schedule_context()["items"] == []
    prompt = supervisor._call_companion_model.call_args.kwargs["system_prompt"]
    assert "秘书和工作协调者" in prompt
    assert "API-A 隔离子代理是实际执行工作的员工" in prompt
    assert "execution_action" in prompt
    assert "不得把计划说成结果" in prompt
    worker_roles = supervisor._call_companion_model.call_args.kwargs["payload"]["worker_roles"]
    assert worker_roles["default_role"] == "general"
    assert "coding" in {item["role"] for item in worker_roles["roles"]}


@pytest.mark.asyncio
async def test_companion_simple_reply_does_not_create_execution_task(tmp_path) -> None:
    supervisor = _make_supervisor(tmp_path)
    supervisor._recall_companion_context = AsyncMock(return_value="")  # type: ignore[method-assign]
    supervisor._persist_companion_turn_pair = AsyncMock(return_value=True)  # type: ignore[method-assign]
    supervisor._call_companion_model = AsyncMock(  # type: ignore[method-assign]
        return_value={
            "reply_text": "在的。",
            "reason": "simple_conversation",
            "schedule_action": {"action": "none"},
            "media_action": {"action": "none"},
            "execution_action": {"action": "none"},
        }
    )

    result = await supervisor.handle_companion_message(text="你在吗？")

    assert result["disposition"] == "respond_to_user"
    assert result["execution_action_result"] is None
    assert supervisor._scheduled_task_store.list(include_completed=True) == []


@pytest.mark.asyncio
async def test_explicit_media_request_is_delegated_when_api_b_omits_media_action(
    tmp_path,
) -> None:
    supervisor = _make_supervisor(tmp_path)
    supervisor._recall_companion_context = AsyncMock(return_value="")  # type: ignore[method-assign]
    supervisor._persist_companion_turn_pair = AsyncMock(return_value=True)  # type: ignore[method-assign]
    supervisor._call_companion_model = AsyncMock(  # type: ignore[method-assign]
        return_value={
            "reply_text": "抱歉，我无法直接播放音乐。",
            "reason": "capability_misclassified",
            "schedule_action": {"action": "none"},
        }
    )

    result = await supervisor.handle_companion_message(text="帮我播放周杰伦的晴天")

    assert result["media_action_result"]["ok"] is True
    assert result["reply_text"] == "我已交给 API-A 查找并播放，执行状态会显示在主 CLI。"
    tasks = supervisor._scheduled_task_store.list(include_completed=True)
    assert len(tasks) == 1
    assert tasks[0]["requested_via"] == "companion_media"
    assert "帮我播放周杰伦的晴天" in tasks[0]["instruction"]


@pytest.mark.asyncio
async def test_companion_media_controls_use_the_web_ui_player_state(tmp_path) -> None:
    supervisor = _make_supervisor(tmp_path)
    supervisor._ui_runtime.enqueue_media(
        {"url": "https://example.com/current.mp3", "title": "当前音频"}
    )
    supervisor._recall_companion_context = AsyncMock(return_value="")  # type: ignore[method-assign]
    supervisor._persist_companion_turn_pair = AsyncMock(return_value=True)  # type: ignore[method-assign]
    supervisor._call_companion_model = AsyncMock(  # type: ignore[method-assign]
        return_value={
            "reply_text": "",
            "schedule_action": {"action": "none"},
        }
    )

    paused = await supervisor.handle_companion_message(text="暂停播放")

    assert paused["media_action_result"]["ok"] is True
    assert paused["media_action_result"]["action"] == "pause"
    assert supervisor._ui_runtime.current_media["playback"] == "paused"
    assert paused["reply_text"] == "已暂停当前播放。"
    assert supervisor._scheduled_task_store.list(include_completed=True) == []


def test_main_cli_media_request_uses_media_label_and_nonpersistent_session(tmp_path) -> None:
    callbacks = []
    host = SimpleNamespace(
        session_id="main-cli",
        _scheduled_execution_active=False,
        _background_task_state=BackgroundTaskState(),
        _autonomous_execution_host=SimpleNamespace(_agent_running=False),
    )

    def start_background(prompt, **kwargs):
        callbacks.append(kwargs["on_complete"])
        assert "即时媒体播放请求" in prompt
        assert "media_play" in prompt
        assert kwargs["task_label"].startswith("媒体请求 ·")
        assert kwargs["response_title"] == "> Voidcube（媒体播放）"
        assert kwargs["persist_session"] is False
        assert kwargs["worker_role"] == "media"
        return True

    host._start_background_agent_task = start_background
    runtime = ScheduledTaskExecutorRuntime(
        _executor_ports(host),
        poll_interval_seconds=0.5,
        outbox_path=tmp_path / "media-writebacks.db",
    )
    runtime._post = Mock(
        side_effect=[
            {
                "status": "claimed",
                "claim": {
                    "task": {
                        "title": "播放媒体 · 测试视频",
                        "instruction": "搜索并播放测试视频",
                        "requested_via": "companion_media",
                        "worker_role": "media",
                    },
                    "run": {"run_id": "media-run-1"},
                },
            },
            {"status": "completed"},
        ]
    )  # type: ignore[method-assign]

    runtime.poll_workflow()
    callbacks[0](True, "已播放", "")

    assert runtime._post.call_args_list[-1].args[0] == "/scheduled-task-runs/media-run-1/finish"


def test_main_cli_companion_delegate_uses_isolated_api_a_prompt(tmp_path) -> None:
    callbacks = []
    host = SimpleNamespace(
        session_id="main-cli",
        _scheduled_execution_active=False,
        _background_task_state=BackgroundTaskState(),
        _autonomous_execution_host=SimpleNamespace(_agent_running=False),
    )

    def start_background(prompt, **kwargs):
        callbacks.append(kwargs["on_complete"])
        assert "API-B 制定计划后转交" in prompt
        assert "隔离的 API-A 子代理" in prompt
        assert "不要创建新的定时任务" in prompt
        assert "不要把请求交给 Auto 自主链" in prompt
        assert kwargs["task_label"] == "API-B 指令 · 检查项目测试"
        assert kwargs["persist_session"] is False
        assert kwargs["worker_role"] == "general"
        return True

    host._start_background_agent_task = start_background
    runtime = ScheduledTaskExecutorRuntime(
        _executor_ports(host),
        poll_interval_seconds=0.5,
        outbox_path=tmp_path / "delegate-writebacks.db",
    )
    runtime._post = Mock(
        side_effect=[
            {
                "status": "claimed",
                "claim": {
                    "task": {
                        "title": "检查项目测试",
                        "instruction": "执行计划并汇总结果",
                        "requested_via": "companion_delegate",
                        "worker_role": "general",
                    },
                    "run": {"run_id": "delegate-run-1"},
                },
            },
            {"status": "completed"},
        ]
    )  # type: ignore[method-assign]

    runtime.poll_workflow()
    callbacks[0](True, "测试通过", "")

    assert runtime._post.call_args_list[-1].args[0] == "/scheduled-task-runs/delegate-run-1/finish"


def test_provider_pool_worker_test_uses_isolated_api_a_prompt(tmp_path) -> None:
    callbacks = []
    host = SimpleNamespace(
        session_id="main-cli",
        _scheduled_execution_active=False,
        _background_task_state=BackgroundTaskState(),
        _autonomous_execution_host=SimpleNamespace(_agent_running=False),
    )

    def start_background(prompt, **kwargs):
        callbacks.append(kwargs["on_complete"])
        kwargs["execution_details"].update(
            {"provider": "research-endpoint", "model": "research-model"}
        )
        assert "Provider 池中的员工连通性测试" in prompt
        assert "隔离的 API-A 子代理" in prompt
        assert "不要进入用户聊天链路" in prompt
        assert kwargs["task_label"] == "员工测试 · 调研员工"
        assert kwargs["response_title"] == "> Voidcube（员工测试）"
        assert kwargs["worker_role"] == "research"
        return True

    host._start_background_agent_task = start_background
    runtime = ScheduledTaskExecutorRuntime(
        _executor_ports(host),
        poll_interval_seconds=0.5,
        outbox_path=tmp_path / "provider-pool-test-writebacks.db",
    )
    runtime._post = Mock(
        side_effect=[
            {
                "status": "claimed",
                "claim": {
                    "task": {
                        "title": "员工测试 · 调研员工",
                        "instruction": "只回复测试成功",
                        "created_by": "api_b",
                        "requested_via": "provider_pool_test",
                        "worker_role": "research",
                    },
                    "run": {"run_id": "provider-pool-test-run-1"},
                },
            },
            {"status": "completed"},
        ]
    )  # type: ignore[method-assign]

    runtime.poll_workflow()
    callbacks[0](True, "测试成功", "")

    assert runtime._post.call_args_list[-1].args[0] == (
        "/scheduled-task-runs/provider-pool-test-run-1/finish"
    )
    writeback = runtime._post.call_args_list[-1].args[1]
    assert writeback["execution_provider"] == "research-endpoint"
    assert writeback["execution_model"] == "research-model"
    assert writeback["elapsed_ms"] >= 0


def test_api_b_scheduled_work_is_projected_as_child_agent_instruction(tmp_path) -> None:
    callbacks = []
    host = SimpleNamespace(
        session_id="main-cli",
        _scheduled_execution_active=False,
        _background_task_state=BackgroundTaskState(),
        _autonomous_execution_host=SimpleNamespace(_agent_running=False),
    )

    def start_background(prompt, **kwargs):
        callbacks.append(kwargs["on_complete"])
        assert "由 API-B 秘书安排并已到期" in prompt
        assert "API-B 只负责传达和安排" in prompt
        assert kwargs["task_label"] == "API-B 指令 · 整理项目进展"
        assert kwargs["response_title"] == "> Voidcube（API-A 子代理）"
        assert kwargs["worker_role"] == "general"
        return True

    host._start_background_agent_task = start_background
    runtime = ScheduledTaskExecutorRuntime(
        _executor_ports(host),
        poll_interval_seconds=0.5,
        outbox_path=tmp_path / "api-b-scheduled-writebacks.db",
    )
    runtime._post = Mock(
        side_effect=[
            {
                "status": "claimed",
                "claim": {
                    "task": {
                        "title": "整理项目进展",
                        "instruction": "读取状态并整理进展",
                        "created_by": "api_b",
                        "requested_via": "companion_voice",
                        "worker_role": "general",
                    },
                    "run": {"run_id": "api-b-scheduled-run-1"},
                },
            },
            {"status": "completed"},
        ]
    )  # type: ignore[method-assign]

    runtime.poll_workflow()
    callbacks[0](True, "已整理", "")

    assert runtime._post.call_args_list[-1].args[0] == (
        "/scheduled-task-runs/api-b-scheduled-run-1/finish"
    )


def test_scheduled_execution_projection_is_compact() -> None:
    from VoidCube_cli.app import VoidcubeCLI

    cli = VoidcubeCLI.__new__(VoidcubeCLI)
    output = []
    with patch("VoidCube_cli.app._cprint", side_effect=output.append):
        cli._announce_scheduled_execution_start(
            1,
            "scheduled-run",
            "full prompt must not be rendered",
            "API-B 指令 · 检查项目测试",
        )
        cli._render_scheduled_execution_completion(
            True,
            "第一行\n第二行 " + "结果" * 120,
            "",
            1,
            "API-B 指令 · 检查项目测试",
            None,
            "full prompt must not be rendered",
        )

    assert output[0] == "  ◇ API-B → API-A 子代理  检查项目测试"
    assert output[1].startswith("  ✓ API-A 子代理  第一行 第二行")
    assert len(output[1]) <= 220
    assert "full prompt" not in "".join(output)


def test_scheduled_host_projects_resolved_worker_label() -> None:
    from VoidCube_cli.scheduled_execution_host import ScheduledExecutionHost

    starts = []
    completions = []
    completed = threading.Event()
    captured_route = {}
    execution_details = {}

    class Agent:
        def run_conversation(self, **_kwargs):
            return {"final_response": "完成"}

    def create_agent(route, *_args):
        captured_route.update(route)
        return Agent()

    host = ScheduledExecutionHost(
        ensure_credentials=lambda: True,
        resolve_agent_route=lambda _prompt, role: {
            "model": "worker-model",
            "runtime": {},
            "worker_role": role,
            "worker_label": "调研员工",
        },
        create_agent=create_agent,
        completion_outcome=lambda result: (
            True,
            str((result or {}).get("final_response") or ""),
            "",
        ),
        announce_start=lambda *_args: starts.append(_args),
        render_completion=lambda *_args: completions.append(_args) or completed.set(),
        invalidate=lambda: None,
    )

    assert host.start(
        "查询资料",
        task_label="API-B 指令 · 核实资料",
        worker_role="research",
        persist_session=False,
        execution_details=execution_details,
    )
    assert completed.wait(2)

    assert starts[0][3] == "API-B 指令 · 调研员工 · 核实资料"
    assert completions[0][4] == "API-B 指令 · 调研员工 · 核实资料"
    assert captured_route["worker_role"] == "research"
    assert execution_details == {"provider": "", "model": "worker-model"}


def test_cli_resolves_scheduled_worker_provider_model_and_toolsets() -> None:
    from VoidCube_cli.app import VoidcubeCLI

    cli = VoidcubeCLI.__new__(VoidcubeCLI)
    cli.config = {"mcp_servers": {}}
    cli.service_tier = None
    cli._resolve_turn_agent_config = Mock(
        return_value={
            "model": "primary-model",
            "runtime": {"provider": "primary", "args": []},
        }
    )
    worker_config = {
        "providers": {
            "research-provider": {"selected_model": "research-model"},
        },
        "companion_workers": {
            "roles": {
                "research": {
                    "provider": "research-provider",
                    "toolsets": ["web", "skills"],
                }
            }
        },
    }
    with (
        patch("VoidCube_app.config.load_config", return_value=worker_config),
        patch(
            "VoidCube_app.runtime_provider.resolve_runtime_provider",
            return_value={
                "provider": "research-provider",
                "base_url": "https://research.example/v1",
                "api_key": "worker-key",
                "args": [],
            },
        ),
    ):
        route = cli._resolve_scheduled_worker_route("查询并核实资料", "research")

    assert route["worker_role"] == "research"
    assert route["model"] == "research-model"
    assert route["runtime"]["provider"] == "research-provider"
    assert route["enabled_toolsets"] == ["web", "skills"]


def test_scheduled_worker_route_failure_is_written_back(tmp_path) -> None:
    host = SimpleNamespace(
        session_id="main-cli",
        _scheduled_execution_active=False,
        _background_task_state=BackgroundTaskState(),
        _autonomous_execution_host=SimpleNamespace(_agent_running=False),
    )
    host._start_background_agent_task = Mock(
        side_effect=ValueError("unknown provider 'missing-provider'")
    )
    runtime = ScheduledTaskExecutorRuntime(
        _executor_ports(host),
        poll_interval_seconds=0.5,
        outbox_path=tmp_path / "worker-route-failure-writebacks.db",
    )
    runtime._post = Mock(
        side_effect=[
            {
                "status": "claimed",
                "claim": {
                    "task": {
                        "title": "调研任务",
                        "instruction": "查询资料",
                        "created_by": "api_b",
                        "requested_via": "companion_delegate",
                        "worker_role": "research",
                    },
                    "run": {"run_id": "worker-route-failure-run"},
                },
            },
            {"status": "failed"},
        ]
    )  # type: ignore[method-assign]

    runtime.poll_workflow()

    finish_call = runtime._post.call_args_list[-1]
    assert finish_call.args[0] == (
        "/scheduled-task-runs/worker-route-failure-run/finish"
    )
    assert finish_call.args[1]["success"] is False
    assert "unknown provider 'missing-provider'" in finish_call.args[1]["error"]
    assert host._scheduled_execution_active is False


def test_main_cli_scheduled_executor_waits_for_running_auto_task(tmp_path) -> None:
    host = SimpleNamespace(
        session_id="main-cli",
        _scheduled_execution_active=False,
        _background_task_state=BackgroundTaskState(),
        _autonomous_execution_host=SimpleNamespace(_agent_running=True),
        _start_background_agent_task=Mock(),
    )
    runtime = ScheduledTaskExecutorRuntime(
        _executor_ports(host),
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


def test_scheduled_executor_does_not_wait_for_foreground_api_a(tmp_path) -> None:
    callbacks = []
    host = SimpleNamespace(
        session_id="main-cli",
        _scheduled_execution_active=False,
        _agent_running=True,
        _command_running=False,
        _background_task_state=BackgroundTaskState(),
        _scheduled_execution_gate=threading.Lock(),
        _autonomous_execution_host=SimpleNamespace(_agent_running=False),
    )
    host._start_background_agent_task = lambda _prompt, **kwargs: callbacks.append(
        kwargs["on_complete"]
    ) or True
    runtime = ScheduledTaskExecutorRuntime(_executor_ports(host), outbox_path=tmp_path / "writebacks.db")
    runtime._post = Mock(
        side_effect=[
            {"claim": {"task": {"title": "计划", "instruction": "执行"}, "run": {"run_id": "run-1"}}},
        ]
    )  # type: ignore[method-assign]

    runtime.poll_workflow()

    runtime._post.assert_called_once()
    assert callbacks


def test_scheduled_executor_does_not_wait_for_manual_background_api_a(tmp_path) -> None:
    background_thread = Mock()
    background_thread.is_alive.return_value = True
    host = SimpleNamespace(
        session_id="main-cli",
        _scheduled_execution_active=False,
        _agent_running=False,
        _command_running=False,
        _background_task_state=BackgroundTaskState(),
        _scheduled_execution_gate=threading.Lock(),
        _autonomous_execution_host=SimpleNamespace(_agent_running=False),
    )
    callbacks = []
    host._start_background_agent_task = lambda _prompt, **kwargs: callbacks.append(
        kwargs["on_complete"]
    ) or True
    host._background_task_state.register_thread(
        "manual-background",
        background_thread,
    )
    runtime = ScheduledTaskExecutorRuntime(_executor_ports(host), outbox_path=tmp_path / "writebacks.db")
    runtime._post = Mock(
        return_value={
            "claim": {"task": {"title": "计划", "instruction": "执行"}, "run": {"run_id": "run-1"}}
        }
    )  # type: ignore[method-assign]

    runtime.poll_workflow()

    runtime._post.assert_called_once()
    assert callbacks


def test_scheduled_executor_holds_scheduled_gate_until_writeback(tmp_path) -> None:
    callbacks = []
    gate = threading.Lock()
    host = SimpleNamespace(
        session_id="main-cli",
        _scheduled_execution_active=False,
        _agent_running=False,
        _command_running=False,
        _background_task_state=BackgroundTaskState(),
        _scheduled_execution_gate=gate,
        _autonomous_execution_host=SimpleNamespace(_agent_running=False),
    )

    def start_background(_prompt, **kwargs):
        callbacks.append(kwargs["on_complete"])
        return True

    host._start_background_agent_task = start_background
    runtime = ScheduledTaskExecutorRuntime(_executor_ports(host), outbox_path=tmp_path / "writebacks.db")
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


def test_scheduled_executor_uses_explicit_bounded_timeouts(tmp_path) -> None:
    callbacks = []
    host = SimpleNamespace(
        session_id="main-cli",
        _scheduled_execution_active=False,
        _agent_running=False,
        _command_running=False,
        _background_task_state=BackgroundTaskState(),
        _scheduled_execution_gate=threading.Lock(),
        _autonomous_execution_host=SimpleNamespace(_agent_running=False),
    )

    def start_background(_prompt, **kwargs):
        callbacks.append(kwargs["on_complete"])
        assert kwargs["request_timeout_seconds"] == 15.0
        assert kwargs["timeout_seconds"] == 45.0
        return True

    host._start_background_agent_task = start_background
    runtime = ScheduledTaskExecutorRuntime(
        _executor_ports(host),
        request_timeout_seconds=15,
        execution_timeout_seconds=45,
        outbox_path=tmp_path / "writebacks.db",
    )
    runtime._post = Mock(
        side_effect=[
            {"claim": {"task": {"title": "计划", "instruction": "执行"}, "run": {"run_id": "run-1"}}},
            {"status": "failed"},
        ]
    )  # type: ignore[method-assign]

    runtime.poll_workflow()
    callbacks[0](False, "", "API-A background execution timed out after 45 seconds")

    finish_payload = runtime._post.call_args_list[-1].args[1]
    assert finish_payload["success"] is False
    assert "timed out after 45 seconds" in finish_payload["error"]


def test_scheduled_executor_writes_structured_rate_limit_metadata(tmp_path) -> None:
    callbacks = []
    host = SimpleNamespace(
        session_id="main-cli",
        _scheduled_execution_active=False,
        _agent_running=False,
        _command_running=False,
        _background_task_state=BackgroundTaskState(),
        _scheduled_execution_gate=threading.Lock(),
        _autonomous_execution_host=SimpleNamespace(_agent_running=False),
    )

    def start_background(_prompt, **kwargs):
        kwargs["execution_details"].update(
            {"provider": "limited-provider", "model": "worker-model"}
        )
        callbacks.append(kwargs["on_complete"])
        return True

    host._start_background_agent_task = start_background
    runtime = ScheduledTaskExecutorRuntime(
        _executor_ports(host), outbox_path=tmp_path / "writebacks.db"
    )
    runtime._post = Mock(
        side_effect=[
            {
                "claim": {
                    "task": {"title": "计划", "instruction": "执行"},
                    "run": {"run_id": "run-1"},
                }
            },
            {"status": "failed"},
        ]
    )  # type: ignore[method-assign]

    runtime.poll_workflow()
    callbacks[0](False, "", "HTTP 429: retry after 45 seconds")

    finish_payload = runtime._post.call_args_list[-1].args[1]
    assert finish_payload["execution_provider"] == "limited-provider"
    assert finish_payload["rate_limited"] is True
    assert finish_payload["error_code"] == 429
    assert finish_payload["retry_after_seconds"] == pytest.approx(45, abs=1)


def test_cli_composes_scheduled_runtime_with_dedicated_gate_and_route():
    from VoidCube_cli.app import VoidcubeCLI

    cli = VoidcubeCLI.__new__(VoidcubeCLI)
    cli._scheduled_execution_gate = threading.Lock()
    cli._scheduled_execution_active = False
    cli._autonomous_execution_host = SimpleNamespace(_agent_running=False)
    cli._scheduled_execution_host = None
    cli.session_id = "main-cli"
    cli._start_scheduled_execution_task = Mock()

    runtime = cli._create_scheduled_executor_runtime()

    assert runtime.ports.execution_gate is cli._scheduled_execution_gate
    assert runtime.ports.start_background_task is cli._start_scheduled_execution_task


def test_scheduled_gate_does_not_stop_foreground_input_loop():
    from VoidCube_cli.app import VoidcubeCLI

    cli = VoidcubeCLI.__new__(VoidcubeCLI)
    cli._scheduled_execution_gate = threading.Lock()
    cli._scheduled_execution_gate.acquire()
    processed: list[object] = []
    stopped = False

    def execute(value: object) -> None:
        nonlocal stopped
        processed.append(value)
        stopped = True

    run_input_process_loop(
        stop_requested=lambda: stopped,
        get_pending_input=lambda _timeout: "foreground input",
        empty_input=queue.Empty,
        perform_idle_maintenance=lambda: None,
        execute_input=execute,
        sleep=lambda _seconds: None,
        report_error=lambda error: (_ for _ in ()).throw(error),
    )

    assert processed == ["foreground input"]
    cli._scheduled_execution_gate.release()


def test_scheduled_task_uses_a_narrow_execution_owner():
    from VoidCube_cli.app import VoidcubeCLI
    from VoidCube_cli.scheduled_execution_host import ScheduledExecutionHost

    parent = VoidcubeCLI.__new__(VoidcubeCLI)
    parent._scheduled_execution_host = None
    parent._ensure_runtime_credentials = lambda: True
    parent._resolve_turn_agent_config = lambda _prompt: {
        "model": "model",
        "runtime": {},
    }
    parent._create_scheduled_agent = Mock()
    parent._invalidate = lambda **_kwargs: None

    owner = parent._ensure_scheduled_execution_host()

    assert isinstance(owner, ScheduledExecutionHost)
    assert not isinstance(owner, VoidcubeCLI)
    for name in ("run", "chat", "_tui_prompt_runtime", "_voice_state"):
        assert not hasattr(owner, name)


def test_scheduled_host_creates_minimal_nonpersistent_agent():
    from VoidCube_cli.app import VoidcubeCLI

    cli = VoidcubeCLI.__new__(VoidcubeCLI)
    cli.max_turns = 3
    cli.enabled_toolsets = ["media"]
    cli.reasoning_config = None
    cli.service_tier = None
    cli._providers_only = None
    cli._providers_ignore = None
    cli._providers_order = None
    cli._provider_sort = None
    cli._provider_require_params = False
    cli._provider_data_collection = None
    cli._session_db = object()
    cli._fallback_model = [{"provider": "fallback", "model": "fallback-model"}]
    captured = {}

    with patch("VoidCube_cli.app._get_AIAgent", return_value=lambda **kwargs: captured.update(kwargs) or object()):
        cli._create_scheduled_agent(
            {
                "model": "agnes-2.5-flash",
                "enabled_toolsets": ["research"],
                "worker_provider_explicit": True,
                "runtime": {
                    "provider": "custom",
                    "base_url": "https://api.agnes-ai.cn/v1",
                    "api_key": "test-key",
                    "args": [],
                },
            },
            "scheduled-run",
            {},
            False,
        )

    assert captured["session_db"] is None
    assert captured["persist_session"] is False
    assert captured["skip_memory"] is True
    assert captured["skip_context_files"] is True
    assert captured["enabled_toolsets"] == ["research"]
    assert captured["fallback_model"] is None


def test_scheduled_store_upgrades_v2_schema_with_worker_role(tmp_path) -> None:
    database = tmp_path / "scheduled_tasks-v2.db"
    with sqlite3.connect(database) as connection:
        connection.execute(
            "CREATE TABLE scheduled_task_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
        )
        connection.execute(
            "CREATE TABLE scheduled_tasks ("
            "schedule_id TEXT PRIMARY KEY, title TEXT NOT NULL, instruction TEXT NOT NULL, "
            "schedule_type TEXT NOT NULL, timezone TEXT NOT NULL DEFAULT '', "
            "status TEXT NOT NULL, created_by TEXT NOT NULL, requested_via TEXT NOT NULL, "
            "created_at TEXT NOT NULL, updated_at TEXT NOT NULL, next_run_at TEXT, "
            "last_run_at TEXT, last_run_status TEXT, active_run_id TEXT, run_at TEXT, "
            "time_of_day TEXT, weekdays_json TEXT NOT NULL DEFAULT '[]')"
        )
        connection.execute(
            "INSERT INTO scheduled_tasks (schedule_id, title, instruction, schedule_type, "
            "status, created_by, requested_via, created_at, updated_at, run_at) "
            "VALUES ('legacy-api-b', '旧任务', '执行旧任务', 'once', 'active', 'api_b', "
            "'companion_voice', '2026-08-09T00:00:00+00:00', "
            "'2026-08-09T00:00:00+00:00', '2026-08-10T00:00:00+00:00')"
        )
        connection.execute(
            "INSERT INTO scheduled_task_meta(key, value) VALUES('schema_version', '2')"
        )

    store = ScheduledTaskStore(database)

    assert store.get("legacy-api-b")["worker_role"] == "general"
    with sqlite3.connect(database) as connection:
        version = connection.execute(
            "SELECT value FROM scheduled_task_meta WHERE key = 'schema_version'"
        ).fetchone()[0]
        columns = {
            row[1]
            for row in connection.execute("PRAGMA table_info(scheduled_tasks)")
        }
        run_columns = {
            row[1]
            for row in connection.execute("PRAGMA table_info(scheduled_task_runs)")
        }
    assert version == "6"
    assert "worker_role" in columns
    assert {"rate_limited", "error_code"} <= run_columns

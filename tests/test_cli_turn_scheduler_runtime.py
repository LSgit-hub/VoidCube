from __future__ import annotations

import pytest
import threading

from VoidCube_app.contracts.scheduler import TurnLane, TurnRequest
from VoidCube_app.turn_scheduler import TurnScheduler
from VoidCube_cli.turn_scheduler_runtime import CliTurnSchedulerPorts, CliTurnSchedulerRuntime


def _runtime(scheduler: TurnScheduler | None = None, calls: list | None = None):
    calls = calls if calls is not None else []
    return CliTurnSchedulerRuntime(
        scheduler or TurnScheduler(),
        CliTurnSchedulerPorts(
            session_id=lambda host: host.session_id,
            tool_policy=lambda _host, _payload, lane: {"lane": lane.value},
            execute_user=lambda host, request, token: calls.append(
                (host, request, token.cancelled)
            ),
            execute_autonomous=lambda host, request, token: calls.append(
                (host, request, token.cancelled)
            ),
            cancel_user=lambda *_args: None,
            cancel_autonomous=lambda *_args: None,
        ),
    )


def test_runtime_submits_user_payload_to_scheduler_executor() -> None:
    calls = []
    runtime = _runtime(calls=calls)
    host = type("Host", (), {"session_id": "session"})()

    assert runtime.submit_user(host, "hello") is True
    assert calls[0][0] is host
    assert calls[0][1].prompt == "hello"
    assert calls[0][1].tool_policy == {"lane": "user_chat"}
    assert calls[0][2] is False
    assert runtime.scheduler.snapshot().active is None


def test_runtime_rejects_autonomous_work_until_enabled() -> None:
    runtime = _runtime()
    host = type("Host", (), {"session_id": "session"})()
    with pytest.raises(RuntimeError, match="gate is closed"):
        runtime.submit_autonomous(host, "auto")
    runtime.enable_autonomous()
    assert runtime.submit_autonomous(host, "auto") is True


def test_runtime_cancel_user_does_not_cancel_autonomous_lane() -> None:
    scheduler = TurnScheduler(autonomous_gate_active=True)
    runtime = _runtime(scheduler)
    scheduler.submit(
        TurnRequest(
            request_id="auto",
            lane=TurnLane.SUPERVISOR_TASK,
            session_id="s",
            prompt="x",
        )
    )
    scheduler.start_next()
    assert runtime.cancel_user() is False


def test_async_runtime_returns_before_active_turn_finishes() -> None:
    entered = threading.Event()
    release = threading.Event()
    calls = []

    def execute(_host, request, _token):
        calls.append(request.prompt)
        entered.set()
        release.wait(1)

    scheduler = TurnScheduler()
    runtime = CliTurnSchedulerRuntime(
        scheduler,
        CliTurnSchedulerPorts(
            session_id=lambda _host: "session",
            tool_policy=lambda *_args: {},
            execute_user=execute,
            execute_autonomous=execute,
            cancel_user=lambda *_args: None,
            cancel_autonomous=lambda *_args: None,
        ),
        asynchronous=True,
    )
    host = type("Host", (), {})()

    assert runtime.submit_user(host, "hello") is True
    assert entered.wait(1)
    assert scheduler.snapshot().active is not None
    assert runtime.cancel_user() is True
    release.set()
    for _ in range(100):
        if scheduler.snapshot().active is None:
            break
        threading.Event().wait(0.01)
    assert scheduler.snapshot().active is None
    assert calls == ["hello"]


def test_runtime_uses_one_executor_for_cancellation() -> None:
    cancelled = []
    runtime = CliTurnSchedulerRuntime(
        TurnScheduler(),
        CliTurnSchedulerPorts(
            session_id=lambda _host: "session",
            tool_policy=lambda *_args: {},
            execute_user=lambda _host, _request, token: token.cancelled,
            execute_autonomous=lambda *_args: None,
            cancel_user=lambda _host, request_id: cancelled.append(request_id),
            cancel_autonomous=lambda *_args: None,
        ),
    )
    host = type("Host", (), {})()
    runtime.submit_user(host, "x")
    assert cancelled == []


def test_runtime_unbinds_request_after_executor_failure() -> None:
    runtime = CliTurnSchedulerRuntime(
        TurnScheduler(),
        CliTurnSchedulerPorts(
            session_id=lambda _host: "session",
            tool_policy=lambda *_args: {},
            execute_user=lambda *_args: (_ for _ in ()).throw(RuntimeError("boom")),
            execute_autonomous=lambda *_args: None,
            cancel_user=lambda *_args: None,
            cancel_autonomous=lambda *_args: None,
        ),
    )
    host = type("Host", (), {})()
    with pytest.raises(RuntimeError, match="boom"):
        runtime.submit_user(host, "x")
    assert runtime._executor._hosts == {}

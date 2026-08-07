from __future__ import annotations

import pytest

from VoidCube_app.contracts.scheduler import TurnLane, TurnRequest
from VoidCube_app.turn_scheduler import TurnScheduler
from VoidCube_cli.turn_scheduler_runtime import CliTurnSchedulerPorts, CliTurnSchedulerRuntime


def _runtime(scheduler: TurnScheduler | None = None, calls: list | None = None):
    calls = calls if calls is not None else []
    return CliTurnSchedulerRuntime(
        scheduler or TurnScheduler(),
        CliTurnSchedulerPorts(
            session_id=lambda host: host.session_id,
            execute_user=lambda host, payload, token: calls.append((host, payload, token.cancelled)),
            execute_autonomous=lambda host, payload, token: calls.append((host, payload, token.cancelled)),
            cancel_user=lambda *_args: None,
            cancel_autonomous=lambda *_args: None,
        ),
    )


def test_runtime_submits_user_payload_to_scheduler_executor() -> None:
    calls = []
    runtime = _runtime(calls=calls)
    host = type("Host", (), {"session_id": "session"})()

    assert runtime.submit_user(host, "hello") is True
    assert calls == [(host, "hello", False)]
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

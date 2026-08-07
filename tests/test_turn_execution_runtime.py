from __future__ import annotations

from VoidCube_cli.turn_execution_runtime import (
    TurnExecutionPorts,
    TurnExecutionRuntime,
)


class _FakeThread:
    def __init__(self, target, *, alive_states):
        self._target = target
        self._alive_states = iter(alive_states)

    def start(self):
        self._target()

    def is_alive(self):
        return next(self._alive_states, False)

    def join(self, _timeout=None):
        return None


def _ports(calls, *, thread_factory, timeout=(False, False)):
    return TurnExecutionPorts(
        interrupt_agent=lambda: calls.append("cancel-agent"),
        check_autonomous_timeout=lambda: timeout,
        cleanup_async_clients=lambda: calls.append("cleanup"),
        flush_stream=lambda: calls.append("flush-stream"),
        flush_output=lambda: calls.append("flush-output"),
        sleep=lambda seconds: calls.append(("sleep", seconds)),
        thread_factory=thread_factory,
    )


def test_turn_execution_runtime_runs_noninteractive_turn_and_flushes():
    calls = []
    runtime = TurnExecutionRuntime(
        _ports(
            calls,
            thread_factory=lambda **kwargs: _FakeThread(
                kwargs["target"], alive_states=[False]
            ),
        )
    )

    result = runtime.execute(lambda: {"final_response": "ok"})

    assert result.result == {"final_response": "ok"}
    assert calls == ["cleanup", "flush-stream", "flush-output", ("sleep", 0.15)]


def test_turn_execution_runtime_interrupts_autonomous_timeout():
    calls = []
    runtime = TurnExecutionRuntime(
        _ports(
            calls,
            thread_factory=lambda **kwargs: _FakeThread(
                kwargs["target"], alive_states=[True, False]
            ),
            timeout=(True, True),
        )
    )

    result = runtime.execute(
        lambda: {"final_response": "timed out"},
        autonomous_task_run_id="run-1",
    )

    assert result.autonomous_timeout_reported is True
    assert result.autonomous_timeout_writeback_succeeded is True
    assert calls[0] == "cancel-agent"

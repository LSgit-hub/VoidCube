from __future__ import annotations

from VoidCube_cli.turn_execution_runtime import (
    TurnExecutionPorts,
    TurnExecutionRuntime,
)
from VoidCube_cli.turn_queue_adapter import (
    InterruptPollResult,
    InterruptPollStatus,
)
from VoidCube_app.turn_queue import TurnInterrupt, TurnInterruptReason


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


def _ports(calls, *, thread_factory, poll=None, timeout=(False, False)):
    return TurnExecutionPorts(
        has_interrupt_queue=lambda: poll is not None,
        poll_interrupt=lambda _defer: poll or InterruptPollResult(
            InterruptPollStatus.EMPTY
        ),
        should_defer_interrupt=lambda: False,
        invalidate=lambda: calls.append("invalidate"),
        interrupt_agent=lambda message: calls.append(("interrupt", message)),
        emit_interrupt_notice=lambda: calls.append("notice"),
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
    assert result.turn_interrupt is None
    assert calls == ["cleanup", "flush-stream", "flush-output", ("sleep", 0.15)]


def test_turn_execution_runtime_interrupts_active_turn():
    calls = []
    interrupt = TurnInterrupt(TurnInterruptReason.NEW_INPUT, "next")
    runtime = TurnExecutionRuntime(
        _ports(
            calls,
            thread_factory=lambda **kwargs: _FakeThread(
                kwargs["target"], alive_states=[True, False]
            ),
            poll=InterruptPollResult(InterruptPollStatus.READY, interrupt=interrupt),
        )
    )

    result = runtime.execute(lambda: {"final_response": "partial"})

    assert result.turn_interrupt is interrupt
    assert calls == ["notice", ("interrupt", "next"), "cleanup", "flush-stream", "flush-output", ("sleep", 0.15)]


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
    assert result.turn_interrupt is not None
    assert result.turn_interrupt.reason is TurnInterruptReason.TIMEOUT
    assert calls[0] == ("interrupt", None)

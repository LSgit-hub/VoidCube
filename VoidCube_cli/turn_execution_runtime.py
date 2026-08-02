"""Thread and interrupt lifecycle for one CLI model turn."""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from threading import Thread
from typing import Any

from VoidCube_cli.turn_queue_adapter import InterruptPollResult, InterruptPollStatus
from VoidCube_app.turn_queue import cancel_turn, TurnInterrupt, TurnInterruptReason


@dataclass(frozen=True, slots=True)
class TurnExecutionPorts:
    """External operations required by the model-turn execution loop."""

    has_interrupt_queue: Callable[[], bool]
    poll_interrupt: Callable[[bool], InterruptPollResult]
    should_defer_interrupt: Callable[[], bool]
    invalidate: Callable[[], None]
    interrupt_agent: Callable[[str | None], None]
    emit_interrupt_notice: Callable[[], None]
    check_autonomous_timeout: Callable[[], tuple[bool, bool]]
    cleanup_async_clients: Callable[[], None]
    flush_stream: Callable[[], None]
    flush_output: Callable[[], None]
    sleep: Callable[[float], None] = time.sleep
    thread_factory: Callable[..., Thread] = Thread


@dataclass(frozen=True, slots=True)
class TurnExecutionResult:
    """Result of the threaded model turn and its observed interruption state."""

    result: Mapping[str, Any] | None
    turn_interrupt: TurnInterrupt | None
    autonomous_timeout_reported: bool
    autonomous_timeout_writeback_succeeded: bool


class TurnExecutionRuntime:
    """Own the worker thread and interrupt-monitoring lifecycle for one turn."""

    def __init__(self, ports: TurnExecutionPorts) -> None:
        self.ports = ports

    def execute(
        self,
        run_agent: Callable[[], Mapping[str, Any] | None],
        *,
        autonomous_task_run_id: str = "",
    ) -> TurnExecutionResult:
        result_holder: dict[str, Mapping[str, Any] | None] = {"result": None}

        def run_agent_thread() -> None:
            result_holder["result"] = run_agent()

        agent_thread = self.ports.thread_factory(
            target=run_agent_thread,
            daemon=True,
        )
        agent_thread.start()

        turn_interrupt: TurnInterrupt | None = None
        autonomous_timeout_reported = False
        autonomous_timeout_writeback_succeeded = False
        while agent_thread.is_alive():
            if autonomous_task_run_id and not autonomous_timeout_reported:
                autonomous_timeout_reported, autonomous_timeout_writeback_succeeded = (
                    self.ports.check_autonomous_timeout()
                )
                if autonomous_timeout_reported:
                    turn_interrupt = cancel_turn(TurnInterruptReason.TIMEOUT)
                    try:
                        self.ports.interrupt_agent(turn_interrupt.agent_message)
                    except Exception:
                        pass

            if self.ports.has_interrupt_queue():
                poll_result = self.ports.poll_interrupt(
                    self.ports.should_defer_interrupt()
                )
                if poll_result.status is InterruptPollStatus.DEFERRED:
                    continue
                if poll_result.interrupt is None:
                    self.ports.invalidate()
                    continue
                turn_interrupt = poll_result.interrupt
                self.ports.emit_interrupt_notice()
                self.ports.interrupt_agent(turn_interrupt.agent_message)
                break
            else:
                agent_thread.join(0.1)

        agent_thread.join()
        self.ports.cleanup_async_clients()
        self.ports.flush_stream()
        self.ports.flush_output()
        self.ports.sleep(0.15)
        return TurnExecutionResult(
            result=result_holder["result"],
            turn_interrupt=turn_interrupt,
            autonomous_timeout_reported=autonomous_timeout_reported,
            autonomous_timeout_writeback_succeeded=autonomous_timeout_writeback_succeeded,
        )

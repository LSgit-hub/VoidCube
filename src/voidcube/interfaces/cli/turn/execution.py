"""Thread and timeout lifecycle for one CLI model turn."""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from threading import Thread
from typing import Any

@dataclass(frozen=True, slots=True)
class TurnExecutionPorts:
    """External operations required by the model-turn execution loop."""

    cleanup_async_clients: Callable[[], None]
    flush_stream: Callable[[], None]
    flush_output: Callable[[], None]
    sleep: Callable[[float], None] = time.sleep
    thread_factory: Callable[..., Thread] = Thread


@dataclass(frozen=True, slots=True)
class TurnExecutionResult:
    """Result of the threaded model turn."""

    result: Mapping[str, Any] | None
    error: BaseException | None = None


class TurnExecutionRuntime:
    """Own the worker thread and interrupt-monitoring lifecycle for one turn."""

    def __init__(self, ports: TurnExecutionPorts) -> None:
        self.ports = ports

    def execute(
        self,
        run_agent: Callable[[], Mapping[str, Any] | None],
    ) -> TurnExecutionResult:
        result_holder: dict[str, Any] = {"result": None, "error": None}

        def run_agent_thread() -> None:
            try:
                result_holder["result"] = run_agent()
            except BaseException as exc:
                result_holder["error"] = exc

        agent_thread = self.ports.thread_factory(
            target=run_agent_thread,
            daemon=True,
        )
        agent_thread.start()

        while agent_thread.is_alive():
            agent_thread.join(0.1)

        agent_thread.join()
        error = result_holder["error"]
        self.ports.cleanup_async_clients()
        self.ports.flush_stream()
        self.ports.flush_output()
        self.ports.sleep(0.15)
        if error is not None:
            raise error
        return TurnExecutionResult(result=result_holder["result"])

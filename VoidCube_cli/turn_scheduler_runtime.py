"""CLI adapter that translates queued prompts into shared scheduler requests."""

from __future__ import annotations

import itertools
from collections.abc import Mapping
from threading import Thread
from dataclasses import dataclass
from typing import Any, Callable

from VoidCube_app.contracts.scheduler import TurnLane, TurnRequest
from VoidCube_app.turn_scheduler import CancellationToken, TurnScheduler
from VoidCube_cli.agent_executor_runtime import (
    CliAgentExecutor,
    CliAgentExecutorPorts,
)


@dataclass(frozen=True, slots=True)
class CliTurnSchedulerPorts:
    session_id: Callable[[Any], str]
    tool_policy: Callable[[Any, Any, TurnLane], Mapping[str, Any]]
    execute_user: Callable[[Any, TurnRequest, CancellationToken], Any]
    execute_autonomous: Callable[[Any, TurnRequest, CancellationToken], Any]
    cancel_user: Callable[[Any, str], None]
    cancel_autonomous: Callable[[Any, str], None]


class CliTurnSchedulerRuntime:
    """Translate CLI host payloads into shared scheduler requests."""

    def __init__(
        self,
        scheduler: TurnScheduler,
        ports: CliTurnSchedulerPorts,
        *,
        asynchronous: bool = False,
        thread_factory: Callable[..., Thread] = Thread,
    ) -> None:
        self.scheduler = scheduler
        self.ports = ports
        self._ids = itertools.count(1)
        self._asynchronous = asynchronous
        self._thread_factory = thread_factory
        self._executor = CliAgentExecutor(
            CliAgentExecutorPorts(
                execute_user=ports.execute_user,
                execute_autonomous=ports.execute_autonomous,
                cancel_user=ports.cancel_user,
                cancel_autonomous=ports.cancel_autonomous,
            )
        )

    def submit_user(self, host: Any, payload: Any) -> bool:
        return self._submit(host, payload, TurnLane.USER_CHAT, "cli")

    def submit_autonomous(self, host: Any, payload: Any) -> bool:
        return self._submit(host, payload, TurnLane.SUPERVISOR_TASK, "autonomous")

    def cancel_user(self) -> bool:
        active = self.scheduler.snapshot().active
        return bool(active and active.lane is TurnLane.USER_CHAT and self.scheduler.cancel(active.request_id))

    def cancel_autonomous(self) -> None:
        self.scheduler.pause_autonomous()

    def enable_autonomous(self) -> None:
        self.scheduler.resume_autonomous()

    def cancel(self, request_id: str) -> None:
        self._executor.cancel(request_id)

    def _request(self, host: Any, payload: Any, lane: TurnLane, source: str) -> TurnRequest:
        session_id = self.ports.session_id(host).strip() or f"{source}-session"
        return TurnRequest(
            request_id=f"{source}-{next(self._ids)}",
            lane=lane,
            session_id=session_id,
            prompt=payload,
            tool_policy=self.ports.tool_policy(host, payload, lane),
            source=source,
        )

    def _submit(self, host: Any, payload: Any, lane: TurnLane, source: str) -> bool:
        request = self._request(host, payload, lane, source)
        if lane is TurnLane.SUPERVISOR_TASK and not self.scheduler.snapshot().autonomous_gate:
            raise RuntimeError("autonomous gate is closed")
        self._executor.bind(request, host)
        if not self._asynchronous:
            return self._run(host, request)
        thread = self._thread_factory(
            target=lambda: self._run(host, request),
            daemon=True,
            name=f"turn-scheduler-{lane.value}",
        )
        try:
            thread.start()
        except Exception:
            self._executor.unbind(request.request_id)
            raise
        return True

    def _run(self, host: Any, request: TurnRequest) -> bool:
        try:
            return self.scheduler.run(request, self._executor.execute)
        finally:
            self._executor.unbind(request.request_id)


__all__ = ["CliTurnSchedulerPorts", "CliTurnSchedulerRuntime"]

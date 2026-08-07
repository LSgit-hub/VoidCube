"""CLI adapter that translates queued prompts into shared scheduler requests."""

from __future__ import annotations

import itertools
from threading import RLock
from threading import Thread
from dataclasses import dataclass
from typing import Any, Callable

from VoidCube_app.contracts.scheduler import TurnLane, TurnRequest
from VoidCube_app.turn_scheduler import CancellationToken, TurnScheduler


@dataclass(frozen=True, slots=True)
class CliTurnSchedulerPorts:
    session_id: Callable[[Any], str]
    execute_user: Callable[[Any, Any, CancellationToken], Any]
    execute_autonomous: Callable[[Any, Any, CancellationToken], Any]
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
        self._active_hosts: dict[str, Any] = {}
        self._active_lanes: dict[str, TurnLane] = {}
        self._lock = RLock()
        self._asynchronous = asynchronous
        self._thread_factory = thread_factory

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
        """Notify the executor host for one scheduler cancellation."""
        with self._lock:
            host = self._active_hosts.get(request_id)
            lane = self._active_lanes.get(request_id)
        if host is None or lane is None:
            return
        callback = (
            self.ports.cancel_user
            if lane is TurnLane.USER_CHAT
            else self.ports.cancel_autonomous
        )
        callback(host, request_id)

    def _request(self, host: Any, payload: Any, lane: TurnLane, source: str) -> TurnRequest:
        session_id = self.ports.session_id(host).strip() or f"{source}-session"
        return TurnRequest(
            request_id=f"{source}-{next(self._ids)}",
            lane=lane,
            session_id=session_id,
            prompt=payload,
            source=source,
        )

    def _submit(self, host: Any, payload: Any, lane: TurnLane, source: str) -> bool:
        request = self._request(host, payload, lane, source)
        if lane is TurnLane.SUPERVISOR_TASK and not self.scheduler.snapshot().autonomous_gate:
            raise RuntimeError("autonomous gate is closed")
        if not self._asynchronous:
            return self._run(host, payload, request)
        thread = self._thread_factory(
            target=lambda: self._run(host, payload, request),
            daemon=True,
            name=f"turn-scheduler-{lane.value}",
        )
        thread.start()
        return True

    def _run(self, host: Any, payload: Any, request: TurnRequest) -> bool:
        with self._lock:
            self._active_hosts[request.request_id] = host
            self._active_lanes[request.request_id] = request.lane

        def execute(turn: TurnRequest, token: CancellationToken) -> Any:
            callback = (
                self.ports.execute_user
                if turn.lane is TurnLane.USER_CHAT
                else self.ports.execute_autonomous
            )
            return callback(host, payload, token)

        try:
            return self.scheduler.run(request, execute)
        finally:
            with self._lock:
                self._active_hosts.pop(request.request_id, None)
                self._active_lanes.pop(request.request_id, None)


__all__ = ["CliTurnSchedulerPorts", "CliTurnSchedulerRuntime"]

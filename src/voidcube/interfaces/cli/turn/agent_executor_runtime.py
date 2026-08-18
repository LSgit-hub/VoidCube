"""CLI-owned AgentExecutor adapter with no scheduler or TUI responsibilities."""

from __future__ import annotations

from dataclasses import dataclass
from threading import RLock
from typing import Any, Callable

from ....domain.contracts.scheduler import TurnLane, TurnRequest
from ....application.scheduling.turn_scheduler import CancellationToken


@dataclass(frozen=True, slots=True)
class CliAgentExecutorPorts:
    execute_user: Callable[[Any, TurnRequest, CancellationToken], Any]
    execute_autonomous: Callable[[Any, TurnRequest, CancellationToken], Any]
    cancel_user: Callable[[Any, str], None]
    cancel_autonomous: Callable[[Any, str], None]


class CliAgentExecutor:
    """Execute request payloads against host-scoped Agent capabilities."""

    def __init__(self, ports: CliAgentExecutorPorts) -> None:
        self.ports = ports
        self._hosts: dict[str, Any] = {}
        self._lanes: dict[str, TurnLane] = {}
        self._lock = RLock()

    def bind(self, request: TurnRequest, host: Any) -> None:
        with self._lock:
            self._hosts[request.request_id] = host
            self._lanes[request.request_id] = request.lane

    def unbind(self, request_id: str) -> None:
        with self._lock:
            self._hosts.pop(request_id, None)
            self._lanes.pop(request_id, None)

    def execute(self, request: TurnRequest, cancellation: CancellationToken) -> Any:
        with self._lock:
            host = self._hosts.get(request.request_id)
        if host is None:
            raise RuntimeError(f"no host bound for request: {request.request_id}")
        callback = (
            self.ports.execute_user
            if request.lane is TurnLane.USER_CHAT
            else self.ports.execute_autonomous
        )
        return callback(host, request, cancellation)

    def cancel(self, request_id: str) -> None:
        with self._lock:
            host = self._hosts.get(request_id)
            lane = self._lanes.get(request_id)
        if host is None or lane is None:
            return
        callback = (
            self.ports.cancel_user
            if lane is TurnLane.USER_CHAT
            else self.ports.cancel_autonomous
        )
        callback(host, request_id)


__all__ = ["CliAgentExecutor", "CliAgentExecutorPorts"]

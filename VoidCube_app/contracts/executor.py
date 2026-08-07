"""UI-independent AgentExecutor contract used by the turn scheduler."""

from __future__ import annotations

from typing import Any, Protocol

from VoidCube_app.contracts.scheduler import TurnRequest
from VoidCube_app.turn_scheduler import CancellationToken


class AgentExecutor(Protocol):
    """Execute one admitted request without owning admission or presentation."""

    def execute(self, request: TurnRequest, cancellation: CancellationToken) -> Any: ...

    def cancel(self, request_id: str) -> None: ...


__all__ = ["AgentExecutor"]

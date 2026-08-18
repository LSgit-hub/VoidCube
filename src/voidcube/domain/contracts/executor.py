"""UI-independent AgentExecutor contract used by the turn scheduler."""

from __future__ import annotations

from typing import Any, Protocol

from .scheduler import TurnRequest


class AgentExecutor(Protocol):
    """Execute one admitted request without owning admission or presentation."""

    def execute(self, request: TurnRequest, cancellation: Any) -> Any: ...

    def cancel(self, request_id: str) -> None: ...


__all__ = ["AgentExecutor"]

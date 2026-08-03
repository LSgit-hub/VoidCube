"""Cache one session hydration result and project ready history through ports."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Optional

from VoidCube_app.session_lifecycle import SessionHydration, SessionHydrationStatus


@dataclass(frozen=True, slots=True)
class CliSessionHydrationPorts:
    """Session cache, repository and history operations supplied by the host."""

    cached_hydration: Callable[[], Optional[SessionHydration]]
    set_hydration: Callable[[SessionHydration], None]
    repository: Callable[[], Any]
    session_id: Callable[[], str]
    set_conversation_history: Callable[[list[dict[str, Any]]], None]
    hydrate: Callable[..., SessionHydration]


class CliSessionHydrationRuntime:
    """Own adapter-side hydration caching without owning session state."""

    def __init__(self, ports: CliSessionHydrationPorts) -> None:
        self.ports = ports

    def load(self) -> tuple[SessionHydration, bool]:
        hydration = self.ports.cached_hydration()
        loaded_now = hydration is None
        if hydration is None:
            hydration = self.ports.hydrate(
                repository=self.ports.repository(),
                session_id=self.ports.session_id(),
            )
            self.ports.set_hydration(hydration)
        if hydration.status is SessionHydrationStatus.READY:
            self.ports.set_conversation_history(
                list(hydration.conversation_history)
            )
        return hydration, loaded_now

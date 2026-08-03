"""Apply shared session lifecycle state through explicit CLI host ports."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from VoidCube_app.session_lifecycle import SessionLifecycleState


@dataclass(frozen=True, slots=True)
class CliSessionLifecyclePorts:
    """Session state and active-agent operations supplied by the host."""

    set_session_id: Callable[[str], None]
    set_session_start: Callable[[datetime], None]
    set_conversation_history: Callable[[list[dict[str, Any]]], None]
    set_pending_title: Callable[[str | None], None]
    set_resumed: Callable[[bool], None]
    clear_hydration: Callable[[], None]
    activate_agent_session: Callable[[str, datetime], None]


class CliSessionLifecycleRuntime:
    """Own lifecycle application ordering without owning CLI state."""

    def __init__(self, ports: CliSessionLifecyclePorts) -> None:
        self.ports = ports

    def apply(self, state: SessionLifecycleState) -> None:
        ports = self.ports
        ports.set_session_id(state.session_id)
        ports.set_session_start(state.session_start)
        ports.set_conversation_history(list(state.conversation_history))
        ports.set_pending_title(state.pending_title)
        ports.set_resumed(state.resumed)
        ports.clear_hydration()
        ports.activate_agent_session(state.session_id, state.session_start)

"""Apply shared session lifecycle state through explicit CLI host ports."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from ...application.sessions import SessionLifecycleState


@dataclass(frozen=True, slots=True)
class CliSessionLifecyclePorts:
    """Session state and active-agent operations supplied by the host."""

    apply_shared_state: Callable[[SessionLifecycleState], None]
    activate_agent_session: Callable[[str, datetime], None]


class CliSessionLifecycleRuntime:
    """Own lifecycle application ordering without owning CLI state."""

    def __init__(self, ports: CliSessionLifecyclePorts) -> None:
        self.ports = ports

    def apply(self, state: SessionLifecycleState) -> None:
        ports = self.ports
        ports.apply_shared_state(state)
        ports.activate_agent_session(state.session_id, state.session_start)

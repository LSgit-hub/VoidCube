"""Finalize the active CLI session through explicit teardown ports."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class CliSessionTeardownPorts:
    """Session repository and interrupted-turn operations supplied by the host."""

    repository: Callable[[], Any]
    session_id: Callable[[], str]
    agent_available: Callable[[], bool]
    agent_running: Callable[[], bool]
    model: Callable[[], Any]
    platform: Callable[[], Any]
    end_session: Callable[[Any, str, str], None]
    invoke_session_end: Callable[..., None]
    log_debug: Callable[[str, BaseException], None]


class CliSessionTeardownRuntime:
    """Own session close and interrupted-session hook error boundaries."""

    def __init__(self, ports: CliSessionTeardownPorts) -> None:
        self.ports = ports

    def close_session(self) -> None:
        ports = self.ports
        repository = ports.repository()
        if repository is None or not ports.agent_available():
            return
        try:
            ports.end_session(repository, ports.session_id(), "cli_close")
        except (Exception, KeyboardInterrupt) as error:
            ports.log_debug("Could not close session in DB: %s", error)

    def finish_interrupted_session(self) -> None:
        ports = self.ports
        if not ports.agent_available() or not ports.agent_running():
            return
        try:
            ports.invoke_session_end(
                session_id=ports.session_id(),
                completed=False,
                interrupted=True,
                model=ports.model(),
                platform=ports.platform() or "cli",
            )
        except Exception:
            pass

"""Session browser and resumed-history adapters for the CLI host."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from .history_display_runtime import (
    CliHistoryDisplayPorts,
    CliHistoryDisplayRuntime,
)
from .session_browser_runtime import (
    CliSessionBrowserPorts,
    CliSessionBrowserRuntime,
)


@dataclass(frozen=True, slots=True)
class CliSessionDisplayPorts:
    """Session data and presentation operations supplied by the CLI host."""

    list_sessions: Callable[..., Sequence[Mapping[str, Any]]]
    active_session_id: Callable[[], str]
    relative_time: Callable[[Any], str]
    conversation_history: Callable[[], Sequence[Mapping[str, Any]]]
    resume_display: Callable[[], str]
    terminal_width: Callable[[], int]
    translate: Callable[..., str]
    emit: Callable[[str], None]
    emit_blank_line: Callable[[], None]
    hydrate_history: Callable[[], None]


class CliSessionDisplayAdapter:
    """Compose session browser and resumed-history runtimes for one CLI host."""

    def __init__(self, ports: CliSessionDisplayPorts) -> None:
        self.ports = ports
        self._browser_runtime: CliSessionBrowserRuntime | None = None

    def browser_runtime(self) -> CliSessionBrowserRuntime:
        runtime = self._browser_runtime
        if runtime is None:
            runtime = CliSessionBrowserRuntime(
                CliSessionBrowserPorts(
                    list_sessions=self.ports.list_sessions,
                    active_session_id=self.ports.active_session_id,
                    relative_time=self.ports.relative_time,
                    translate=self.ports.translate,
                    emit=self.ports.emit,
                )
            )
            self._browser_runtime = runtime
        return runtime

    def list_recent(self, limit: int = 10) -> list[dict[str, Any]]:
        return self.browser_runtime().list_recent(limit=limit)

    def show_recent(self, *, reason: str = "history", limit: int = 8) -> bool:
        return self.browser_runtime().show_recent(reason=reason, limit=limit)

    def display_history(self) -> None:
        self.ports.hydrate_history()
        CliHistoryDisplayRuntime(
            CliHistoryDisplayPorts(
                conversation_history=self.ports.conversation_history,
                resume_display=self.ports.resume_display,
                terminal_width=self.ports.terminal_width,
                translate=self.ports.translate,
                emit=self.ports.emit,
                emit_blank_line=self.ports.emit_blank_line,
            )
        ).run()


__all__ = ["CliSessionDisplayAdapter", "CliSessionDisplayPorts"]

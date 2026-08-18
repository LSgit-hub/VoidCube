"""Render the interactive resumed-session preload outcome through ports."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from ...application.sessions import (
    SessionHydration,
    SessionHydrationStatus,
)


@dataclass(frozen=True, slots=True)
class CliSessionResumePorts:
    """Resume state, hydration and terminal operations supplied by the host."""

    resumed: Callable[[], bool]
    repository_available: Callable[[], bool]
    session_id: Callable[[], str]
    hydrate: Callable[[], tuple[SessionHydration, bool]]
    accent_color: Callable[[], str]
    translate: Callable[..., str]
    emit: Callable[[str], None]


class CliSessionResumeRuntime:
    """Own interactive resume outcome projection without owning CLI state."""

    def __init__(self, ports: CliSessionResumePorts) -> None:
        self.ports = ports

    def preload(self) -> bool:
        ports = self.ports
        if not ports.resumed() or not ports.repository_available():
            return False

        hydration, _ = ports.hydrate()
        if hydration.status is SessionHydrationStatus.MISSING:
            ports.emit(f"[bold red]Session not found: {ports.session_id()}[/]")
            ports.emit(
                "[dim]Use a session ID from a previous CLI run "
                "(VoidCube sessions list).[/]"
            )
            return False

        if hydration.status is SessionHydrationStatus.READY:
            restored = hydration.conversation_history
            msg_count = sum(message.get("role") == "user" for message in restored)
            title_part = ""
            if hydration.metadata and hydration.metadata.get("title"):
                title_part = f' "{hydration.metadata["title"]}"'
            accent_color = ports.accent_color()
            ports.emit(
                f"[{accent_color}]↻ {ports.translate('prompts.resumed_session', default='Resumed session')} "
                f"[bold]{ports.session_id()}[/bold]"
                f"{title_part} ({msg_count} "
                f"{ports.translate('prompts.user_messages', default='user message')}"
                f"{'s' if msg_count != 1 else ''}, {len(restored)} "
                f"{ports.translate('prompts.total_messages', default='total messages')})[/]"
            )
            return True

        accent_color = ports.accent_color()
        ports.emit(
            f"[{accent_color}]Session {ports.session_id()} found but has no "
            "messages. Starting fresh.[/]"
        )
        return False

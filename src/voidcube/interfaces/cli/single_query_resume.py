"""Project single-query resume hydration status through explicit ports."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from VoidCube_app.session_lifecycle import (
    SessionHydration,
    SessionHydrationStatus,
)


@dataclass(frozen=True, slots=True)
class CliSingleQueryResumePorts:
    """Resume result and terminal operations supplied by the CLI host."""

    session_id: Callable[[], str]
    accent_color: Callable[[], str]
    escape: Callable[[str], str]
    translate: Callable[..., str]
    emit: Callable[[str], None]


class CliSingleQueryResumeRuntime:
    """Own single-query resume status output without owning session state."""

    def __init__(self, ports: CliSingleQueryResumePorts) -> None:
        self.ports = ports

    def report(self, hydration: SessionHydration, loaded_now: bool) -> bool:
        ports = self.ports
        if hydration.status is SessionHydrationStatus.MISSING:
            if loaded_now:
                ports.emit(f"\033[1;31mSession not found: {ports.session_id()}\033[0m")
                ports.emit(
                    "\033[2mUse a session ID from a previous CLI run "
                    "(VoidCube sessions list).\033[0m"
                )
            return False

        if not loaded_now:
            return True

        if hydration.status is SessionHydrationStatus.READY:
            restored = hydration.conversation_history
            msg_count = sum(message.get("role") == "user" for message in restored)
            title_part = ""
            if hydration.metadata and hydration.metadata.get("title"):
                title_part = f' "{hydration.metadata["title"]}"'
            ports.emit(
                f"[bold {ports.accent_color()}]↻ "
                f"{ports.translate('prompts.resumed_session', default='Resumed session')}[/] "
                f"[bold]{ports.escape(ports.session_id())}[/]"
                f"[bold {ports.accent_color()}]{ports.escape(title_part)}[/] "
                f"({msg_count} "
                f"{ports.translate('prompts.user_messages', default='user message')}"
                f"{'s' if msg_count != 1 else ''}, {len(restored)} "
                f"{ports.translate('prompts.total_messages', default='total messages')})"
            )
            return True

        ports.emit(
            f"[bold {ports.accent_color()}]Session "
            f"{ports.escape(ports.session_id())} found but has no messages. "
            "Starting fresh.[/]"
        )
        return True

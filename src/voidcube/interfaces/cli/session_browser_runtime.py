"""List and render recent CLI sessions through explicit host ports."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class CliSessionBrowserPorts:
    """Session query and terminal operations supplied by the CLI host."""

    list_sessions: Callable[..., Sequence[Mapping[str, Any]]]
    active_session_id: Callable[[], str]
    relative_time: Callable[[Any], str]
    translate: Callable[..., str]
    emit: Callable[[str], None]


class CliSessionBrowserRuntime:
    """Own recent-session filtering and compact in-chat presentation."""

    def __init__(self, ports: CliSessionBrowserPorts) -> None:
        self.ports = ports

    def list_recent(self, limit: int = 10) -> list[dict[str, Any]]:
        try:
            sessions = self.ports.list_sessions(
                source="cli",
                exclude_sources=["tool"],
                limit=limit,
                exclude_id_prefixes=["scheduled_"],
            )
        except Exception:
            return []
        active_session_id = self.ports.active_session_id()
        return [
            dict(session)
            for session in sessions
            if session.get("id") != active_session_id
        ]

    def show_recent(self, *, reason: str = "history", limit: int = 8) -> bool:
        sessions = self.list_recent(limit=limit)
        if not sessions:
            return False

        emit = self.ports.emit
        emit("")
        if reason == "history":
            emit(
                self.ports.translate(
                    "no_messages_in_the_current_chat_yet_here_are_recent_sessions_you_can_resume"
                )
            )
        else:
            emit(self.ports.translate("recent_sessions"))
        emit("")
        emit(f"  #  {'Title':<30} {'Preview':<38} {'Last Active':<13} {'ID'}")
        emit(f"  ─ {'─' * 30} {'─' * 38} {'─' * 13} {'─' * 24}")
        for index, session in enumerate(sessions, 1):
            title = (session.get("title") or "—")[:28]
            preview = (session.get("preview") or "")[:36]
            last_active = self.ports.relative_time(session.get("last_active"))
            emit(
                f"  {index}  {title:<30} {preview:<38} "
                f"{last_active:<13} {session['id']}"
            )
        emit("")
        emit(self.ports.translate("use_resume_session_id_or_title_to_continue_where_you_left_off"))
        emit("  You can also use /resume <number> to resume by the number above!")
        emit("")
        return True

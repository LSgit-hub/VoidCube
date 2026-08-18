"""Render the resumable session summary when the CLI exits."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any


@dataclass(frozen=True, slots=True)
class CliExitSummaryPorts:
    """Session data and terminal operations supplied by the CLI host."""

    conversation_history: Callable[[], Sequence[Mapping[str, Any]]]
    session_id: Callable[[], str]
    session_start: Callable[[], datetime]
    now: Callable[[], datetime]
    session_title: Callable[[], str | None]
    translate: Callable[..., str]
    emit: Callable[[str], None]
    emit_blank_line: Callable[[], None]


class CliExitSummaryRuntime:
    """Own exit summary formatting without owning session state."""

    def __init__(self, ports: CliExitSummaryPorts) -> None:
        self.ports = ports

    def render(self) -> None:
        ports = self.ports
        ports.emit_blank_line()
        history = ports.conversation_history()
        if not history:
            ports.emit("bye.")
            return

        user_messages = sum(1 for message in history if message.get("role") == "user")
        tool_calls = sum(
            1
            for message in history
            if message.get("role") == "tool" or message.get("tool_calls")
        )
        elapsed = max(0, int((ports.now() - ports.session_start()).total_seconds()))
        hours, remainder = divmod(elapsed, 3600)
        minutes, seconds = divmod(remainder, 60)
        if hours > 0:
            duration = f"{hours}h {minutes}m {seconds}s"
        elif minutes > 0:
            duration = f"{minutes}m {seconds}s"
        else:
            duration = f"{seconds}s"

        try:
            title = ports.session_title()
        except Exception:
            title = None
        session_id = ports.session_id()
        ports.emit(
            ports.translate(
                "prompts.resume_session_with",
                default="Resume this session with:",
            )
        )
        ports.emit(f"  VoidCube --resume {session_id}")
        if title:
            ports.emit(f"  VoidCube -c \"{title}\"")
        ports.emit_blank_line()
        ports.emit(f"{ports.translate('prompts.session', default='Session')}:        {session_id}")
        if title:
            ports.emit(f"{ports.translate('prompts.title', default='Title')}:          {title}")
        ports.emit(f"{ports.translate('prompts.duration', default='Duration')}:       {duration}")
        ports.emit(
            f"{ports.translate('prompts.messages', default='Messages')}:       "
            f"{len(history)} ({user_messages} user, {tool_calls} tool calls)"
        )

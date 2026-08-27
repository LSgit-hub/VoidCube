"""Render the interactive CLI startup sequence through explicit ports."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any


DEFAULT_STARTUP_HISTORY_LIMIT = 4
MIN_STARTUP_HISTORY_LIMIT = 1
MAX_STARTUP_HISTORY_LIMIT = 10


def normalize_startup_history_limit(value: object) -> int:
    try:
        limit = int(value)
    except (TypeError, ValueError):
        return DEFAULT_STARTUP_HISTORY_LIMIT
    if not MIN_STARTUP_HISTORY_LIMIT <= limit <= MAX_STARTUP_HISTORY_LIMIT:
        return DEFAULT_STARTUP_HISTORY_LIMIT
    return limit


@dataclass(frozen=True, slots=True)
class CliStartupPorts:
    """Startup state, data and terminal operations supplied by the host."""

    terminal_lines: Callable[[], int]
    write_blank_lines: Callable[[int], None]
    show_banner: Callable[[], None]
    resumed: Callable[[], bool]
    preload_resumed_session: Callable[[], bool]
    display_resumed_history: Callable[[], None]
    recent_sessions: Callable[[], Sequence[Mapping[str, object]]]
    history_limit: Callable[[], int]
    terminal_width: Callable[[], int]
    render_history_panel: Callable[[list[str]], None]
    tools_count: Callable[[], int]
    skills_count: Callable[[], int]
    session_id: Callable[[], str | None]
    preloaded_skills: Callable[[], Sequence[str]]
    startup_skills_line_shown: Callable[[], bool]
    set_startup_skills_line_shown: Callable[[bool], None]
    accent_hex: Callable[[], str]
    emit: Callable[[str], None]


class CliStartupRuntime:
    """Own interactive startup presentation without owning CLI state."""

    def __init__(self, ports: CliStartupPorts) -> None:
        self.ports = ports

    def run(self) -> None:
        self._pin_content_to_terminal_bottom()
        self.ports.show_banner()
        if self.ports.resumed() and self.ports.preload_resumed_session():
            self.ports.display_resumed_history()

        sessions = [
            session
            for session in self.ports.recent_sessions()
            if session.get("id") != self.ports.session_id()
        ][: normalize_startup_history_limit(self.ports.history_limit())]
        if sessions:
            self.ports.render_history_panel(self._history_lines(sessions))
        else:
            self.ports.emit("[dim]暂无对话历史[/]")

        self.ports.emit(
            f"[#FFF8DC]{self.ports.tools_count()} 个工具 · "
            f"{self.ports.skills_count()} 技能 · "
            f"当前会话: {self.ports.session_id() or '新会话'}[/]"
        )
        skills = self.ports.preloaded_skills()
        if skills and not self.ports.startup_skills_line_shown():
            self.ports.emit(
                f"[bold {self.ports.accent_hex()}]Activated skills:[/] "
                f"{', '.join(skills)}"
            )
            self.ports.set_startup_skills_line_shown(True)
        self.ports.emit("")

    def _pin_content_to_terminal_bottom(self) -> None:
        lines = self.ports.terminal_lines()
        if lines > 2:
            self.ports.write_blank_lines(lines - 1)

    def _history_lines(
        self,
        sessions: Sequence[Mapping[str, object]],
    ) -> list[str]:
        lines = [f"[bold {self.ports.accent_hex()}]历史会话列表[/]", ""]
        width = self.ports.terminal_width()
        for index, session in enumerate(sessions, 1):
            session_id = str(session.get("id") or "")
            preview = str(session.get("preview") or "")
            title = str(session.get("title") or "")
            display_text = title or preview
            id_part = f"{index}.ID: {session_id}"
            separator = " | "
            max_preview_length = width - len(id_part) - len(separator) - 4
            if len(display_text) > max_preview_length:
                display_text = display_text[: max_preview_length - 3] + "..."
            lines.append(f"  {id_part}{separator}{display_text}")
        return lines


def render_compact_history_panel(
    console: Any,
    lines: Sequence[str],
) -> None:
    from rich.panel import Panel

    console.print(
        Panel(
            "\n".join(lines),
            border_style="dim",
            padding=(0, 1),
        )
    )

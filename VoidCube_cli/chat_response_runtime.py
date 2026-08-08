"""Response and reasoning presentation for one completed CLI turn."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from rich import box as rich_box
from rich.panel import Panel

from VoidCube_cli.style import ACCENT, BORDER, RESPONSE_LABEL, TEXT


_DIM = "\033[2m"
_RST = "\033[0m"


@dataclass(frozen=True, slots=True)
class ChatResponsePorts:
    """Presentation operations supplied by the CLI UI owner."""

    should_emit_scrollback: Callable[[], bool]
    show_reasoning: Callable[[], bool]
    reasoning_already_shown: Callable[[], bool]
    terminal_width: Callable[[], int]
    emit: Callable[[str], None]
    create_console: Callable[[], Any]
    rich_text_from_ansi: Callable[[str], Any]
    bell_on_complete: Callable[[], bool]
    bell: Callable[[], None]


class ChatResponseRuntime:
    """Render reasoning and final response without owning CLI state."""

    def __init__(self, ports: ChatResponsePorts) -> None:
        self.ports = ports

    def render(
        self,
        *,
        response: str,
        response_previewed: bool,
        failed: bool,
        partial: bool,
        stream_started: bool,
        response_box_open: bool,
        reasoning: str,
    ) -> None:
        if not self.ports.should_emit_scrollback():
            return

        if self.ports.show_reasoning() and not self.ports.reasoning_already_shown():
            self._render_reasoning(reasoning)

        if response and not response_previewed:
            already_streamed = stream_started and response_box_open and not (failed or partial)
            if not already_streamed:
                self._render_response_panel(response)

        if self.ports.bell_on_complete():
            self.ports.bell()

    def _render_reasoning(self, reasoning: str) -> None:
        if not reasoning:
            return
        width = self.ports.terminal_width()
        label = " Reasoning "
        top = f"{_DIM}┌─{label}{'─' * max(width - 2 - len(label) - 1, 0)}┐{_RST}"
        bottom = f"{_DIM}└{'─' * (width - 2)}┘{_RST}"
        lines = reasoning.strip().splitlines()
        if len(lines) > 10:
            display = "\n".join(lines[:10])
            display += f"\n{_DIM}  ... ({len(lines) - 10} more lines){_RST}"
        else:
            display = reasoning.strip()
        self.ports.emit(f"\n{top}\n{_DIM}{display}{_RST}\n{bottom}")

    def _render_response_panel(self, response: str) -> None:
        color = ACCENT
        text_color = TEXT
        self.ports.create_console().print(
            Panel(
                self.ports.rich_text_from_ansi(response),
                title=f"[{color} bold]{RESPONSE_LABEL}[/]",
                title_align="left",
                border_style=BORDER,
                style=text_color,
                box=rich_box.ROUNDED,
                padding=(1, 2),
            )
        )

from __future__ import annotations

import re
import shutil
import textwrap
from collections.abc import Callable

from VoidCube_cli.chat_render_state import CliStreamRenderState
from VoidCube_cli.chat_stream_processor import (
    append_reasoning_lines,
    append_text_lines,
    consume_stream_delta,
    drain_reasoning_preview,
    flush_reasoning_line,
    flush_stream_filter,
    flush_text_line,
)


_DIM = "\033[2m"
_RESET = "\033[0m"


class CliStreamRenderer:
    """Own terminal rendering for one CLI stream state."""

    def __init__(
        self,
        state: CliStreamRenderState,
        *,
        emit_line: Callable[[str], None],
        should_emit: Callable[[], bool],
        show_reasoning: Callable[[], bool],
        verbose: Callable[[], bool],
        terminal_width: Callable[[], int] | None = None,
    ) -> None:
        self.state = state
        self._emit_line = emit_line
        self._should_emit = should_emit
        self._show_reasoning = show_reasoning
        self._verbose = verbose
        self._terminal_width = terminal_width or _default_terminal_width

    def emit_reasoning_preview(self, reasoning_text: str) -> None:
        preview_text = reasoning_text.strip()
        if not preview_text:
            return

        prefix = "  [思考中] "
        wrap_width = max(30, self._terminal_width() - len(prefix) - 2)
        paragraphs = []
        raw_paragraphs = re.split(r"\n\s*\n+", preview_text.replace("\r\n", "\n"))
        for paragraph in raw_paragraphs:
            compact = " ".join(
                line.strip() for line in paragraph.splitlines() if line.strip()
            )
            if compact:
                paragraphs.append(textwrap.fill(compact, width=wrap_width))
        preview_text = "\n".join(paragraphs)
        if not preview_text:
            return

        if self._verbose():
            self._emit_line(f"  {_DIM}[思考中] {preview_text}{_RESET}")
            return

        lines = preview_text.splitlines()
        if len(lines) > 5:
            preview = "\n".join(lines[:5])
            preview += f"\n  ... ({len(lines) - 5} more lines)"
        else:
            preview = preview_text
        self._emit_line(f"  {_DIM}[思考中] {preview}{_RESET}")

    def flush_reasoning_preview(self, *, force: bool = False) -> None:
        target_width = max(
            40,
            self._terminal_width() - len("  [思考中] ") - 4,
        )
        preview = drain_reasoning_preview(
            self.state,
            target_width=target_width,
            force=force,
        )
        if preview:
            self.emit_reasoning_preview(preview)

    def stream_reasoning_delta(self, text: str) -> None:
        if not self._should_emit() or not text:
            return

        self.state.reasoning_shown_this_turn = True
        if self.state.response_box_open:
            return

        if not self.state.reasoning_box_open:
            self.state.reasoning_box_open = True
            width = self._terminal_width()
            label = " Reasoning "
            fill = width - 2 - len(label)
            self._emit_line(
                f"\n{_DIM}┌─{label}{'─' * max(fill - 1, 0)}┐{_RESET}"
            )

        for line in append_reasoning_lines(self.state, text):
            self._emit_line(f"{_DIM}{line}{_RESET}")

    def close_reasoning_box(self) -> None:
        if not self._should_emit():
            self.state.reasoning_box_open = False
            self.state.reasoning_buffer = ""
            self.state.deferred_content = ""
            return
        if not self.state.reasoning_box_open:
            return

        buffered = flush_reasoning_line(self.state)
        if buffered:
            self._emit_line(f"{_DIM}{buffered}{_RESET}")
        self._emit_line(
            f"{_DIM}└{'─' * (self._terminal_width() - 2)}┘{_RESET}"
        )
        self.state.reasoning_box_open = False

        deferred = self.state.deferred_content
        if deferred:
            self.state.deferred_content = ""
            self.emit_stream_text(deferred)

    def stream_delta(self, text: str | None) -> None:
        if not self._should_emit():
            if text is None:
                self.state.reset_stream()
            elif text:
                self.state.started = True
            return
        if text is None:
            self.flush_stream()
            self.state.reset_stream()
            return
        if not text:
            return

        for segment in consume_stream_delta(
            self.state,
            text,
            show_reasoning=self._show_reasoning(),
        ):
            if segment.kind == "reasoning":
                self.stream_reasoning_delta(segment.text)
            else:
                self.emit_stream_text(segment.text)

    def emit_stream_text(self, text: str) -> None:
        if not self._should_emit() or not text:
            return

        if self._show_reasoning() and self.state.reasoning_box_open:
            self.state.deferred_content += text
            return

        self.close_reasoning_box()

        if not self.state.response_box_open:
            text = text.lstrip("\n")
            if not text:
                return
            self.state.response_box_open = True
            label, text_ansi = _response_style()
            self.state.text_ansi = text_ansi
            fill = self._terminal_width() - 2 - len(label)
            self._emit_line(
                f"\n{_response_accent()}╭─{label}{'─' * max(fill - 1, 0)}╮{_RESET}"
            )

        for line in append_text_lines(self.state, text):
            self._emit_response_line(line)

    def flush_stream(self) -> None:
        if not self._should_emit():
            self.state.text_buffer = ""
            self.state.response_box_open = False
            self.state.prefilter_buffer = ""
            self.state.in_reasoning_block = False
            return

        for segment in flush_stream_filter(self.state):
            self.emit_stream_text(segment.text)
        self.close_reasoning_box()

        buffered = flush_text_line(self.state)
        if buffered:
            self._emit_response_line(buffered)
        if self.state.response_box_open:
            self._emit_line(
                f"{_response_accent()}╰{'─' * (self._terminal_width() - 2)}╯{_RESET}"
            )

    def _emit_response_line(self, text: str) -> None:
        if self.state.text_ansi:
            self._emit_line(f"{self.state.text_ansi}{text}{_RESET}")
        else:
            self._emit_line(text)


def _default_terminal_width() -> int:
    try:
        return shutil.get_terminal_size().columns
    except Exception:
        return 80


def _response_style() -> tuple[str, str]:
    try:
        from VoidCube_cli.skin_engine import get_active_skin

        skin = get_active_skin()
        label = skin.get_branding("response_label", "> Voidcube")
        text_hex = skin.get_color("banner_text", "#FFF8DC")
    except Exception:
        label = "> Voidcube"
        text_hex = "#FFF8DC"
    try:
        red = int(text_hex[1:3], 16)
        green = int(text_hex[3:5], 16)
        blue = int(text_hex[5:7], 16)
        text_ansi = f"\033[38;2;{red};{green};{blue}m"
    except (ValueError, IndexError):
        text_ansi = ""
    return label, text_ansi


def _response_accent() -> str:
    try:
        from VoidCube_cli.cli_ui import _ACCENT

        return str(_ACCENT)
    except Exception:
        return "\033[1;38;2;48;54;61m"

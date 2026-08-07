from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(slots=True)
class CliStreamRenderState:
    """Mutable rendering state for one CLI response stream."""

    text_buffer: str = ""
    started: bool = False
    response_box_open: bool = False
    text_ansi: str = ""
    prefilter_buffer: str = ""
    last_was_newline: bool = True
    in_reasoning_block: bool = False
    reasoning_box_open: bool = False
    reasoning_buffer: str = ""
    reasoning_preview_buffer: str = ""
    deferred_content: str = ""
    reasoning_shown_this_turn: bool = False
    in_code_fence: bool = False
    code_fence_language: str = ""
    code_fence_lines: list[str] = field(default_factory=list)

    def reset_stream(self) -> None:
        """Reset one model invocation while preserving user-turn history."""
        self.text_buffer = ""
        self.started = False
        self.response_box_open = False
        self.text_ansi = ""
        self.prefilter_buffer = ""
        self.last_was_newline = True
        self.in_reasoning_block = False
        self.reasoning_box_open = False
        self.reasoning_buffer = ""
        self.reasoning_preview_buffer = ""
        self.deferred_content = ""
        self.in_code_fence = False
        self.code_fence_language = ""
        self.code_fence_lines.clear()

    def begin_turn(self) -> None:
        """Reset all rendering state at the start of a user turn."""
        self.reset_stream()
        self.reasoning_shown_this_turn = False

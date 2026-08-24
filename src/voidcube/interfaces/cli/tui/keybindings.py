"""Stateless text-editing keybindings for the terminal adapter."""

from __future__ import annotations

from prompt_toolkit.buffer import Buffer
from collections.abc import Callable

from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.key_binding.key_processor import KeyPressEvent
from prompt_toolkit.filters import Condition


def accept_completion_or_suggestion(buffer: Buffer) -> None:
    """Accept the selected completion or suggestion, or start completion."""
    if buffer.complete_state:
        completion = buffer.complete_state.current_completion
        if completion is None:
            buffer.go_to_completion(0)
            completion = buffer.complete_state and buffer.complete_state.current_completion
        if completion is not None:
            buffer.apply_completion(completion)
    elif buffer.suggestion and buffer.suggestion.text:
        buffer.insert_text(buffer.suggestion.text)
    else:
        buffer.start_completion()


def install_text_editing_keybindings(
    key_bindings: KeyBindings,
    *,
    normal_input_active: Callable[[], bool],
) -> None:
    """Install multiline-entry and completion bindings behind the modal-state filter."""
    normal_input = Condition(normal_input_active)

    @key_bindings.add("escape", "enter", filter=normal_input)
    def insert_alt_enter_newline(event: KeyPressEvent) -> None:
        event.current_buffer.insert_text("\n")

    @key_bindings.add("c-j", filter=normal_input)
    def insert_ctrl_enter_newline(event: KeyPressEvent) -> None:
        event.current_buffer.insert_text("\n")

    @key_bindings.add("tab", eager=True, filter=normal_input)
    def handle_tab(event: KeyPressEvent) -> None:
        accept_completion_or_suggestion(event.current_buffer)


def navigate_history(buffer: Buffer, *, direction: str, count: int) -> None:
    """Move through multiline input or history using prompt-toolkit's buffer."""
    if direction == "up":
        buffer.auto_up(count=count)
    else:
        buffer.auto_down(count=count)


def install_history_navigation_keybindings(
    key_bindings: KeyBindings,
    *,
    normal_input_active: Callable[[], bool],
) -> None:
    """Install history navigation behind an explicit modal-state predicate."""
    normal_input = Condition(normal_input_active)

    @key_bindings.add("up", filter=normal_input)
    def history_up(event: KeyPressEvent) -> None:
        navigate_history(event.current_buffer, direction="up", count=event.arg)

    @key_bindings.add("down", filter=normal_input)
    def history_down(event: KeyPressEvent) -> None:
        navigate_history(event.current_buffer, direction="down", count=event.arg)

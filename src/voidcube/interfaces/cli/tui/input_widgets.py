"""Prompt-toolkit input presentation for the terminal CLI adapter."""

from __future__ import annotations

import shutil
from collections.abc import Callable
from dataclasses import dataclass

from prompt_toolkit.application import get_app
from prompt_toolkit.auto_suggest import AutoSuggestFromHistory
from prompt_toolkit.filters import Condition
from prompt_toolkit.formatted_text import AnyFormattedText
from prompt_toolkit.history import FileHistory
from prompt_toolkit.layout.dimension import Dimension
from prompt_toolkit.layout.processors import (
    ConditionalProcessor,
    PasswordProcessor,
    Processor,
    Transformation,
    TransformationInput,
)
from prompt_toolkit.utils import get_cwidth
from prompt_toolkit.widgets import TextArea

from ..commands.catalog import SlashCommandAutoSuggest, SlashCommandCompleter


@dataclass(frozen=True, slots=True)
class InputWidgetPorts:
    """Read-only callbacks and values needed to construct the input area."""

    history_path: str
    prompt_fragments: Callable[[], AnyFormattedText]
    prompt_text: Callable[[], str]
    command_available: Callable[[str], bool]
    command_running: Callable[[], bool]
    password_mask_active: Callable[[], bool]
    input_locked: Callable[[], bool] = lambda: False


def build_input_area(*, ports: InputWidgetPorts) -> TextArea:
    """Build the multiline input widget without receiving the CLI host."""
    completer = SlashCommandCompleter(
        skill_commands_provider=None,
        command_filter=ports.command_available,
    )
    input_area = TextArea(
        height=Dimension(min=1, max=8, preferred=1),
        prompt=ports.prompt_fragments,
        style="class:input-area",
        multiline=True,
        wrap_lines=True,
        read_only=Condition(lambda: ports.command_running() or ports.input_locked()),
        history=FileHistory(ports.history_path),
        completer=completer,
        complete_while_typing=True,
        auto_suggest=SlashCommandAutoSuggest(
            history_suggest=AutoSuggestFromHistory(),
            completer=completer,
        ),
    )

    def input_height() -> int:
        try:
            document = input_area.buffer.document
            prompt_width = max(2, get_cwidth(ports.prompt_text()))
            try:
                terminal_size = get_app().output.get_size()
                terminal_columns = terminal_size.columns
                terminal_rows = terminal_size.rows
            except Exception:
                terminal_size = shutil.get_terminal_size((80, 24))
                terminal_columns = terminal_size.columns
                terminal_rows = terminal_size.lines
            available_width = max(1, terminal_columns - prompt_width)
            max_input_height = max(1, min(8, terminal_rows - 3))
            visual_lines = 0
            for line in document.lines:
                line_width = get_cwidth(line)
                visual_lines += 1 if line_width <= 0 else max(1, -(-line_width // available_width))
            return min(max(visual_lines, 1), max_input_height)
        except Exception:
            return 1

    input_area.window.height = input_height
    input_area.control.input_processors.append(
        ConditionalProcessor(
            PasswordProcessor(),
            filter=Condition(ports.password_mask_active),
        )
    )
    return input_area


def install_placeholder_processor(
    input_area: TextArea,
    *,
    placeholder_text: Callable[[], str],
) -> None:
    """Display a placeholder after the prompt while the input buffer is empty."""
    input_area.control.input_processors.append(_PlaceholderProcessor(placeholder_text))


class _PlaceholderProcessor(Processor):
    def __init__(self, placeholder_text: Callable[[], str]) -> None:
        self._placeholder_text = placeholder_text

    def apply_transformation(
        self,
        transformation_input: TransformationInput,
    ) -> Transformation:
        document = transformation_input.document
        fragments = transformation_input.fragments
        if not document.text and transformation_input.lineno == 0:
            text = self._placeholder_text()
            if text:
                return Transformation(fragments=fragments + [("class:placeholder", text)])
        return Transformation(fragments=fragments)

"""Compose completed-turn response rendering."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from VoidCube_cli.chat_response_runtime import ChatResponsePorts, ChatResponseRuntime


@dataclass(frozen=True, slots=True)
class CliChatFinalizationPorts:
    """Response presentation and follow-up queue operations supplied by the host."""

    should_emit_scrollback: Callable[[], bool]
    show_reasoning: Callable[[], bool]
    reasoning_already_shown: Callable[[], bool]
    terminal_width: Callable[[], int]
    emit: Callable[[str], None]
    create_console: Callable[[], Any]
    rich_text_from_ansi: Callable[[str], Any]
    bell_on_complete: Callable[[], bool]
    bell: Callable[[], None]


class CliChatFinalizationRuntime:
    """Own finalization ordering without owning display or queue state."""

    def __init__(self, ports: CliChatFinalizationPorts) -> None:
        self.ports = ports

    def finalize(
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
        ports = self.ports
        ChatResponseRuntime(
            ChatResponsePorts(
                should_emit_scrollback=ports.should_emit_scrollback,
                show_reasoning=ports.show_reasoning,
                reasoning_already_shown=ports.reasoning_already_shown,
                terminal_width=ports.terminal_width,
                emit=ports.emit,
                create_console=ports.create_console,
                rich_text_from_ansi=ports.rich_text_from_ansi,
                bell_on_complete=ports.bell_on_complete,
                bell=ports.bell,
            )
        ).render(
            response=response,
            response_previewed=response_previewed,
            failed=failed,
            partial=partial,
            stream_started=stream_started,
            response_box_open=response_box_open,
            reasoning=reasoning,
        )

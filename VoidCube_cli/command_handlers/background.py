"""Manual background task command handler."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from VoidCube_cli.command_router import ParsedCliCommand


@dataclass(frozen=True, slots=True)
class BackgroundCommandText:
    usage: str
    example: str
    description: str


@dataclass(frozen=True, slots=True)
class BackgroundCommandPorts:
    start_background: Callable[[str], bool]
    emit: Callable[[str], None]
    text: BackgroundCommandText


def handle_background_command(
    request: ParsedCliCommand,
    *,
    ports: BackgroundCommandPorts,
) -> None:
    """Start an isolated background task when a prompt is supplied."""
    prompt = request.arguments.strip()
    if not prompt:
        ports.emit(ports.text.usage)
        ports.emit(ports.text.example)
        ports.emit(ports.text.description)
        return
    ports.start_background(prompt)

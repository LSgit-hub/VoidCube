"""Ephemeral side-question command handler."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from VoidCube_cli.command_router import ParsedCliCommand


@dataclass(frozen=True, slots=True)
class BtwCommandText:
    usage: str
    example: str
    description: str


@dataclass(frozen=True, slots=True)
class BtwCommandPorts:
    start_btw: Callable[[str], bool]
    emit: Callable[[str], None]
    text: BtwCommandText


def handle_btw_command(request: ParsedCliCommand, *, ports: BtwCommandPorts) -> None:
    """Start an ephemeral session-context side question when supplied."""
    question = request.arguments.strip()
    if not question:
        ports.emit(ports.text.usage)
        ports.emit(ports.text.example)
        ports.emit(ports.text.description)
        return
    ports.start_btw(question)

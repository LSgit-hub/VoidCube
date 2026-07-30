"""Autonomous gate command handler."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from VoidCube_cli.command_router import ParsedCliCommand


@dataclass(frozen=True, slots=True)
class AutonomousCommandPorts:
    activate: Callable[[str], None]
    deactivate: Callable[[], None]


def handle_auto_command(
    request: ParsedCliCommand,
    *,
    ports: AutonomousCommandPorts,
) -> None:
    """Request temporary autonomous-gate activation with an optional focus."""
    ports.activate(request.arguments.strip())


def handle_auto_q_command(
    _request: ParsedCliCommand,
    *,
    ports: AutonomousCommandPorts,
) -> None:
    """Request temporary autonomous-gate deactivation."""
    ports.deactivate()

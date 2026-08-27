"""Explicit CLI tool-progress display mode selection."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from ..router import ParsedCliCommand


VERBOSE_MODES = ("off", "new", "all", "verbose")


@dataclass(frozen=True, slots=True)
class VerboseCommandPorts:
    """Callbacks required to inspect and change tool-progress display."""

    mode: Callable[[], str]
    set_mode: Callable[[str], None]
    emit: Callable[[str], None]


def handle_verbose_command(
    request: ParsedCliCommand,
    *,
    ports: VerboseCommandPorts,
) -> None:
    """Show or explicitly select one of the four tool-progress modes."""
    argument = request.arguments.strip().lower()
    if not argument:
        _render_status(ports)
        return

    if argument not in VERBOSE_MODES:
        ports.emit(f"  Unknown tool progress mode: {argument}")
        ports.emit("  Usage: /verbose [off|new|all|verbose]")
        return

    ports.set_mode(argument)


def _render_status(ports: VerboseCommandPorts) -> None:
    current = str(ports.mode() or "all").strip().lower()
    if current not in VERBOSE_MODES:
        current = "all"
    ports.emit(f"  Tool progress: {current.upper()}")
    ports.emit("  Usage: /verbose [off|new|all|verbose]")


__all__ = ["VERBOSE_MODES", "VerboseCommandPorts", "handle_verbose_command"]

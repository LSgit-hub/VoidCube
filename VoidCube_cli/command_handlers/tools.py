"""Tool configuration command handler."""

from __future__ import annotations

import shlex
from dataclasses import dataclass
from typing import Callable, Sequence

from VoidCube_cli.command_router import ParsedCliCommand


@dataclass(frozen=True, slots=True)
class ToolsCommandText:
    usage: Callable[[str], str]
    builtin_example: Callable[[str], str]
    mcp_example: Callable[[str], str]
    changing: Callable[[str, Sequence[str]], str]
    session_reset: str


@dataclass(frozen=True, slots=True)
class ToolsCommandPorts:
    render_catalog: Callable[[], None]
    list_configuration: Callable[[], None]
    change_configuration: Callable[[str, Sequence[str]], None]
    load_enabled_toolsets: Callable[[], Sequence[str] | None]
    set_enabled_toolsets: Callable[[Sequence[str] | None], None]
    reset_session: Callable[[], None]
    emit: Callable[[str], None]
    text: ToolsCommandText


def handle_tools_command(
    request: ParsedCliCommand,
    *,
    ports: ToolsCommandPorts,
) -> None:
    """Route the `/tools` catalog and configuration operations."""
    parts = _split_command(request.original)
    subcommand = parts[1] if len(parts) > 1 else ""
    if subcommand not in {"list", "disable", "enable"}:
        ports.render_catalog()
        return

    if subcommand == "list":
        ports.list_configuration()
        return

    names = tuple(parts[2:])
    if not names:
        ports.emit(ports.text.usage(subcommand))
        ports.emit(ports.text.builtin_example(subcommand))
        ports.emit(ports.text.mcp_example(subcommand))
        return

    ports.emit(ports.text.changing(subcommand, names))
    ports.change_configuration(subcommand, names)
    ports.set_enabled_toolsets(ports.load_enabled_toolsets())
    ports.reset_session()
    ports.emit(ports.text.session_reset)


def _split_command(command: str) -> list[str]:
    try:
        return shlex.split(command)
    except ValueError:
        return command.split()

"""Display-state command handlers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from VoidCube_cli.command_router import ParsedCliCommand


@dataclass(frozen=True, slots=True)
class StatusBarCommandPorts:
    visible: Callable[[], bool]
    set_visible: Callable[[bool], None]
    emit: Callable[[str], None]


def handle_statusbar_command(
    request: ParsedCliCommand,
    *,
    ports: StatusBarCommandPorts,
) -> None:
    del request
    visible = not ports.visible()
    ports.set_visible(visible)
    ports.emit(f"  Status bar {'visible' if visible else 'hidden'}")

"""Operational command handlers with explicit runtime ports."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Mapping, Sequence

from VoidCube_cli.command_router import ParsedCliCommand


@dataclass(frozen=True, slots=True)
class StopCommandPorts:
    list_processes: Callable[[], Sequence[Mapping[str, object]]]
    kill_all: Callable[[], int]
    emit: Callable[[str], None]
    no_running_message: str
    stopping_message: Callable[[int], str]
    stopped_message: Callable[[int], str]


def handle_stop_command(
    request: ParsedCliCommand,
    *,
    ports: StopCommandPorts,
) -> None:
    del request
    running_count = sum(
        process.get("status") == "running"
        for process in ports.list_processes()
    )
    if not running_count:
        ports.emit(ports.no_running_message)
        return
    ports.emit(ports.stopping_message(running_count))
    ports.emit(ports.stopped_message(ports.kill_all()))

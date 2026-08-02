"""Handle the terminal suspend key without depending on the CLI host."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class SuspendKeybindingPorts:
    """Platform and process operations supplied by the terminal host."""

    platform: Callable[[], str]
    emit: Callable[[str], None]
    invalidate: Callable[[Any], None]
    run_in_terminal: Callable[[Callable[[], None]], None]
    suspend_process: Callable[[], None]


class SuspendKeybindingRuntime:
    """Own Ctrl+Z routing for supported and unsupported platforms."""

    def __init__(self, ports: SuspendKeybindingPorts) -> None:
        self.ports = ports

    def handle(self, event: Any) -> None:
        if self.ports.platform() == "win32":
            self.ports.emit("\nSuspend (Ctrl+Z) is not supported on Windows.")
            self.ports.invalidate(event)
            return
        self.ports.run_in_terminal(self.ports.suspend_process)

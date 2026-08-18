"""Resolve command availability from explicit model capability ports."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class CliCommandAvailabilityPorts:
    model: Callable[[], Any | None]
    supports_fast_mode: Callable[[Any | None], bool]


class CliCommandAvailabilityRuntime:
    """Project model capabilities into CLI and TUI command availability."""

    def __init__(self, ports: CliCommandAvailabilityPorts) -> None:
        self.ports = ports

    def fast_available(self) -> bool:
        return bool(self.ports.supports_fast_mode(self.ports.model()))

    def available(self, slash_command: str) -> bool:
        if slash_command == "/fast":
            return self.fast_available()
        return True

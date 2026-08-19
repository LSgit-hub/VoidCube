"""Project chat outer exceptions and autonomous turn error writeback."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class CliChatErrorPorts:
    """Error state and terminal operations supplied by the CLI host."""

    should_emit: Callable[[], bool]
    translate: Callable[..., str]
    emit: Callable[[str], None]


class CliChatErrorRuntime:
    """Own outer chat error projection without owning autonomous state."""

    def __init__(self, ports: CliChatErrorPorts) -> None:
        self.ports = ports

    def handle(self, error: Exception) -> dict[str, Any]:
        ports = self.ports
        error_result: dict[str, Any] = {
            "failed": True,
            "partial": False,
            "interrupted": False,
            "error": str(error),
            "response": "",
        }
        if ports.should_emit():
            ports.emit(
                ports.translate(
                    "chat_error.error",
                    error=str(error),
                )
            )
        return error_result

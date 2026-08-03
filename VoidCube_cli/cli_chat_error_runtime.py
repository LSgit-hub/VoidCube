"""Project chat outer exceptions and autonomous turn error writeback."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class CliChatErrorPorts:
    """Error state and terminal operations supplied by the CLI host."""

    autonomous_timeout_reported: bool
    autonomous_task_run_id: str
    autonomous_timeout_writeback_succeeded: bool
    current_autonomous_task: Callable[[], Any]
    set_last_agent_turn_result: Callable[[Mapping[str, Any]], None]
    should_emit: Callable[[], bool]
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
        if ports.autonomous_timeout_reported:
            error_result.update(
                {
                    "interrupted": True,
                    "error": "Autonomous task timed out after 30 minutes.",
                }
            )
        if (
            ports.autonomous_task_run_id
            and not ports.autonomous_timeout_writeback_succeeded
        ):
            error_result["autonomous_task_run_id"] = ports.autonomous_task_run_id
            ports.set_last_agent_turn_result(error_result)
        elif ports.current_autonomous_task() is None:
            ports.set_last_agent_turn_result(error_result)
        if ports.should_emit():
            ports.emit(f"Error: {error}")
        return error_result

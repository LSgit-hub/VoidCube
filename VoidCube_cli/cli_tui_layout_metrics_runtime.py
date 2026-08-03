"""Provide shared terminal-width and compact-layout metrics for the TUI."""

from __future__ import annotations

import shutil
from collections.abc import Callable
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class CliTuiLayoutMetricsPorts:
    """CLI state readers used by layout metrics."""

    agent_running: Callable[[], bool]
    spinner_visible: Callable[[], bool]


class CliTuiLayoutMetricsRuntime:
    """Own terminal-size policy without owning CLI state."""

    def __init__(self, ports: CliTuiLayoutMetricsPorts) -> None:
        self.ports = ports

    @staticmethod
    def terminal_width(default: tuple[int, int] = (80, 24)) -> int:
        try:
            from prompt_toolkit.application import get_app

            return get_app().output.get_size().columns
        except Exception:
            return shutil.get_terminal_size(default).columns

    def minimal_chrome(self, width: int | None = None) -> bool:
        return (self.terminal_width() if width is None else width) < 64

    def input_rule_height(self, position: str, width: int | None = None) -> int:
        if position not in {"top", "bottom"}:
            raise ValueError(f"Unknown input rule position: {position}")
        if position == "top":
            return 1
        return 0 if self.minimal_chrome(width) else 1

    def agent_spacer_height(self, width: int | None = None) -> int:
        if not self.ports.agent_running():
            return 0
        return 0 if self.minimal_chrome(width) else 1

    def spinner_height(self, width: int | None = None) -> int:
        if not self.ports.spinner_visible():
            return 0
        return 0 if self.minimal_chrome(width) else 1

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

    MINIMAL_WIDTH = 64
    COMPACT_HEIGHT = 16
    MINIMUM_STATUS_HEIGHT = 16
    EXTENDED_PANEL_HEIGHT = 20

    def __init__(self, ports: CliTuiLayoutMetricsPorts) -> None:
        self.ports = ports

    @staticmethod
    def terminal_width(default: tuple[int, int] = (80, 24)) -> int:
        try:
            from prompt_toolkit.application import get_app

            return get_app().output.get_size().columns
        except Exception:
            return shutil.get_terminal_size(default).columns

    @staticmethod
    def terminal_height(default: tuple[int, int] = (80, 24)) -> int:
        try:
            from prompt_toolkit.application import get_app

            return get_app().output.get_size().rows
        except Exception:
            return shutil.get_terminal_size(default).lines

    def minimal_chrome(
        self,
        width: int | None = None,
        height: int | None = None,
    ) -> bool:
        active_width = self.terminal_width() if width is None else width
        active_height = self.terminal_height() if height is None else height
        return (
            active_width < self.MINIMAL_WIDTH
            or active_height < self.COMPACT_HEIGHT
        )

    def input_rule_height(
        self,
        position: str,
        width: int | None = None,
        height: int | None = None,
    ) -> int:
        if position not in {"top", "bottom"}:
            raise ValueError(f"Unknown input rule position: {position}")
        if position == "top":
            active_height = self.terminal_height() if height is None else height
            return 1 if active_height >= self.MINIMUM_STATUS_HEIGHT else 0
        return 0 if self.minimal_chrome(width, height) else 1

    def agent_spacer_height(
        self,
        width: int | None = None,
        height: int | None = None,
    ) -> int:
        if not self.ports.agent_running():
            return 0
        return 0 if self.minimal_chrome(width, height) else 1

    def spinner_height(
        self,
        width: int | None = None,
        height: int | None = None,
    ) -> int:
        if not self.ports.spinner_visible():
            return 0
        return 0 if self.minimal_chrome(width, height) else 1

    def status_bar_visible(self, height: int | None = None) -> bool:
        active_height = self.terminal_height() if height is None else height
        return active_height >= self.MINIMUM_STATUS_HEIGHT

    def extended_panels_visible(
        self,
        width: int | None = None,
        height: int | None = None,
    ) -> bool:
        active_width = self.terminal_width() if width is None else width
        active_height = self.terminal_height() if height is None else height
        return (
            active_width >= self.MINIMAL_WIDTH
            and active_height >= self.EXTENDED_PANEL_HEIGHT
        )

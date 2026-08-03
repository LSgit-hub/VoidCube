"""Project CLI attachment state into the TUI image indicator."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from prompt_toolkit.formatted_text import AnyFormattedText


@dataclass(frozen=True, slots=True)
class CliTuiImageIndicatorPorts:
    attached_images: Callable[[], list[Path]]
    image_counter: Callable[[], int]
    format_badges: Callable[[list[Path], int], str]


class CliTuiImageIndicatorRuntime:
    """Build image indicator fragments from explicit attachment ports."""

    def __init__(self, ports: CliTuiImageIndicatorPorts) -> None:
        self.ports = ports

    def fragments(self) -> AnyFormattedText:
        images = self.ports.attached_images()
        if not images:
            return []
        badges = self.ports.format_badges(images, self.ports.image_counter())
        return [("class:image-badge", f" {badges} ")]

    def visible(self) -> bool:
        return bool(self.ports.attached_images())

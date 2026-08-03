"""Render the compact voice status footer from explicit state ports."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass


StatusFragment = tuple[str, str]


@dataclass(frozen=True, slots=True)
class CliVoiceStatusPorts:
    """Voice state and terminal layout callbacks supplied by the CLI host."""

    terminal_width: Callable[[], int]
    minimal_chrome: Callable[[int], bool]
    recording: Callable[[], bool]
    processing: Callable[[], bool]
    continuous: Callable[[], bool]


class CliVoiceStatusRuntime:
    """Own voice status text selection without owning voice state."""

    def __init__(self, ports: CliVoiceStatusPorts) -> None:
        self.ports = ports

    def build(self, width: int | None = None) -> list[StatusFragment]:
        width = width or self.ports.terminal_width()
        compact = self.ports.minimal_chrome(width)
        if self.ports.recording():
            if compact:
                return [("class:voice-status-recording", " ● REC ")]
            return [("class:voice-status-recording", " ● REC  Ctrl+B to stop ")]
        if self.ports.processing():
            if compact:
                return [("class:voice-status", " ◉ STT ")]
            return [("class:voice-status", " ◉ Transcribing... ")]
        if compact:
            return [("class:voice-status", " 🎤 Ctrl+B ")]
        continuous = " | Continuous" if self.ports.continuous() else ""
        return [
            (
                "class:voice-status",
                f" 🎤 Voice mode{continuous}  —  Ctrl+B to record ",
            )
        ]

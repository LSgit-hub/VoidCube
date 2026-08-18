"""Coordinate the terminal push-to-talk key."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class VoiceKeybindingPorts:
    """Voice state and asynchronous operations supplied by the host."""

    voice_mode: Callable[[], bool]
    recording: Callable[[], bool]
    set_continuous: Callable[[bool], None]
    agent_running: Callable[[], bool]
    modal_active: Callable[[], bool]
    processing: Callable[[], bool]
    start_recording: Callable[[], None]
    stop_recording: Callable[[], None]
    run_background: Callable[[Callable[[], None]], None]
    invalidate: Callable[[Any], None]
    invalidate_app: Callable[[], None]
    report_error: Callable[[Exception], None]


class VoiceKeybindingRuntime:
    """Own push-to-talk guards without directly accessing the CLI host."""

    def __init__(self, ports: VoiceKeybindingPorts) -> None:
        self.ports = ports

    def handle(self, event: Any) -> None:
        if not self.ports.voice_mode():
            return

        if self.ports.recording():
            self.ports.set_continuous(False)
            self.ports.invalidate(event)
            self.ports.run_background(self.ports.stop_recording)
            return

        if (
            self.ports.agent_running()
            or self.ports.modal_active()
            or self.ports.processing()
        ):
            return

        self.ports.set_continuous(True)

        def start() -> None:
            try:
                self.ports.start_recording()
                self.ports.invalidate_app()
            except Exception as error:
                self.ports.report_error(error)

        self.ports.run_background(start)
        self.ports.invalidate(event)

"""Voice-mode command handler."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from VoidCube_cli.command_router import ParsedCliCommand


@dataclass(frozen=True, slots=True)
class VoiceCommandPorts:
    enable: Callable[[], None]
    disable: Callable[[], None]
    tts_unavailable: Callable[[], None]
    show_status: Callable[[], None]
    voice_mode_enabled: Callable[[], bool]
    emit: Callable[[str], None]


def handle_voice_command(request: ParsedCliCommand, *, ports: VoiceCommandPorts) -> None:
    """Dispatch the voice command without owning its runtime session."""
    subcommand = request.arguments.lower().strip()
    if subcommand == "on":
        ports.enable()
    elif subcommand == "off":
        ports.disable()
    elif subcommand == "tts":
        ports.tts_unavailable()
    elif subcommand == "status":
        ports.show_status()
    elif not subcommand:
        (ports.disable if ports.voice_mode_enabled() else ports.enable)()
    else:
        ports.emit(f"Unknown voice subcommand: {subcommand}")
        ports.emit("Usage: /voice [on|off|tts|status]")

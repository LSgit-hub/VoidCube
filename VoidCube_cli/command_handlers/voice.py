"""Voice-mode command handler."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from VoidCube_cli.command_router import ParsedCliCommand


@dataclass(frozen=True, slots=True)
class VoiceCommandPorts:
    enable: Callable[[], None]
    disable: Callable[[], None]
    tts_status: Callable[[], None]
    tts_speak: Callable[[str], None]
    show_status: Callable[[], None]
    voice_mode_enabled: Callable[[], bool]
    emit: Callable[[str], None]


def handle_voice_command(request: ParsedCliCommand, *, ports: VoiceCommandPorts) -> None:
    """Dispatch the voice command without owning its runtime session."""
    arguments = request.arguments.strip()
    command, _, text = arguments.partition(" ")
    subcommand = command.lower()
    if subcommand == "on" and not text:
        ports.enable()
    elif subcommand == "off" and not text:
        ports.disable()
    elif subcommand == "tts" and not text:
        ports.tts_status()
    elif subcommand == "tts" and text.strip():
        ports.tts_speak(text.strip())
    elif subcommand == "status" and not text:
        ports.show_status()
    elif not arguments:
        (ports.disable if ports.voice_mode_enabled() else ports.enable)()
    else:
        ports.emit(f"Unknown voice subcommand: {arguments}")
        ports.emit("Usage: /voice [on|off|tts [text]|status]")

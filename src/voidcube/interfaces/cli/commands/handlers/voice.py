"""Voice-mode command handler."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from ..router import ParsedCliCommand


@dataclass(frozen=True, slots=True)
class VoiceCommandPorts:
    enable: Callable[[], None]
    disable: Callable[[], None]
    tts_status: Callable[[], None]
    tts_speak: Callable[[str], None]
    show_status: Callable[[], None]
    voice_mode_enabled: Callable[[], bool]
    emit: Callable[[str], None]
    show_help: Callable[[], None] = lambda: None
    set_target: Callable[[str], None] = lambda _target: None
    start_session: Callable[[], None] = lambda: None
    interrupt_session: Callable[[], None] = lambda: None
    start_continuous: Callable[[], None] = lambda: None
    stop_continuous: Callable[[], None] = lambda: None


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
    elif subcommand in {"help", "?"} and not text:
        ports.show_help()
    elif subcommand == "target" and text.strip():
        ports.set_target(text.strip())
    elif subcommand in {"supervisor", "web", "api-b", "apib"} and not text:
        ports.set_target("supervisor")
        ports.enable()
    elif subcommand in {"terminal", "cli", "local"} and not text:
        ports.set_target("terminal")
    elif subcommand == "session" and not text:
        ports.start_session()
    elif subcommand in {"interrupt", "cancel"} and not text:
        ports.interrupt_session()
    elif subcommand == "continuous" and text.strip().lower() == "on":
        ports.start_continuous()
    elif subcommand == "continuous" and text.strip().lower() == "off":
        ports.stop_continuous()
    elif not arguments:
        (ports.disable if ports.voice_mode_enabled() else ports.enable)()
    else:
        ports.emit(f"Unknown voice subcommand: {arguments}")
        ports.emit(
            "Usage: /voice [on|off|status|target terminal|target supervisor|"
            "supervisor|terminal|session|interrupt|continuous on|continuous off|tts [text]|help]"
        )

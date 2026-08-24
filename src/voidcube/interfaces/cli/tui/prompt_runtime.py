"""Project interactive CLI state into prompt-toolkit prompt fragments."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class CliTuiPromptPorts:
    """State readers and terminal presentation policies for the prompt."""

    voice_recording: Callable[[], bool]
    voice_processing: Callable[[], bool]
    sudo_active: Callable[[], bool]
    secret_active: Callable[[], bool]
    approval_active: Callable[[], bool]
    clarify_freetext: Callable[[], bool]
    clarify_active: Callable[[], bool]
    command_running: Callable[[], bool]
    command_spinner_frame: Callable[[], str]
    agent_running: Callable[[], bool]
    voice_mode: Callable[[], bool]
    minimal_tui_chrome: Callable[[int], bool]
    terminal_width: Callable[[], int]
    audio_status: Callable[[], Mapping[str, object]]


class CliTuiPromptRuntime:
    """Render prompt state without owning interactive CLI state."""

    def __init__(self, ports: CliTuiPromptPorts) -> None:
        self.ports = ports
        # The sticky profile name never changes mid-session; cache it so the
        # per-frame prompt path does not stat ~/.VoidCube/active_profile on
        # every repaint.
        self._profile_name: str | None = None
        self._profile_loaded = False

    def _active_profile_name(self) -> str | None:
        if not self._profile_loaded:
            self._profile_loaded = True
            try:
                from ....infrastructure.config.profiles import get_active_profile_name

                self._profile_name = get_active_profile_name()
            except Exception:
                self._profile_name = None
        return self._profile_name

    def fragments(self) -> list[tuple[str, str]]:
        symbol, state_suffix = self.prompt_symbols()
        ports = self.ports
        compact = ports.minimal_tui_chrome(ports.terminal_width())

        def state_fragment(style: str, icon: str, extra: str = "") -> list[tuple[str, str]]:
            if compact:
                text = icon
                if extra:
                    text = f"{text} {extra.strip()}".rstrip()
                return [(style, text + " ")]
            if extra:
                return [(style, f"{icon} {extra} {state_suffix}")]
            return [(style, f"{icon} {state_suffix}")]

        if ports.voice_recording():
            return state_fragment("class:voice-recording", "●", self.audio_level_bar())
        if ports.voice_processing():
            return state_fragment("class:voice-processing", "◉")
        if ports.sudo_active():
            return state_fragment("class:sudo-prompt", "🔐")
        if ports.secret_active():
            return state_fragment("class:sudo-prompt", "🔑")
        if ports.approval_active():
            return state_fragment("class:prompt-working", "⚠")
        if ports.clarify_freetext():
            return state_fragment("class:clarify-selected", "✎")
        if ports.clarify_active():
            return state_fragment("class:prompt-working", "?")
        if ports.command_running():
            return state_fragment("class:prompt-working", ports.command_spinner_frame())
        if ports.agent_running():
            return state_fragment("class:prompt-working", ">")
        if ports.voice_mode():
            return state_fragment("class:voice-prompt", "🎤")
        return [("class:prompt", symbol)]

    def text(self) -> str:
        return "".join(text for _, text in self.fragments())

    def prompt_symbols(self) -> tuple[str, str]:
        symbol = "❯ "
        profile = self._active_profile_name()
        if profile and profile not in ("default", "custom"):
            symbol = f"{profile} {symbol}"

        stripped = symbol.rstrip()
        if not stripped:
            return "❯ ", "❯ "
        parts = stripped.split()
        candidate = parts[-1] if parts else ""
        arrow_chars = ("❯", ">", "$", "#", "›", "»", "→")
        if any(ch in candidate for ch in arrow_chars):
            return symbol, candidate.rstrip() + " "
        return symbol, symbol

    def audio_level_bar(self) -> str:
        level_bars = " ▁▂▃▄▅▆▇"
        try:
            rms = float(self.ports.audio_status().get("audio_rms", 0.0))
        except Exception:
            return ""
        level = max(0, min(7, int(rms * 7)))
        return level_bars[level]

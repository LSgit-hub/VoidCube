"""Render dynamic placeholder, hint, and spinner text for the terminal UI."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class TuiDynamicTextPorts:
    """Read-only state and presentation callbacks supplied by the CLI host."""

    voice_recording: Callable[[], bool]
    voice_processing: Callable[[], bool]
    sudo_active: Callable[[], bool]
    secret_active: Callable[[], bool]
    approval_active: Callable[[], bool]
    clarify_freetext: Callable[[], bool]
    clarify_active: Callable[[], bool]
    command_running: Callable[[], bool]
    command_spinner_frame: Callable[[], str]
    command_status: Callable[[], str]
    agent_running: Callable[[], bool]
    voice_mode: Callable[[], bool]
    spinner_text: Callable[[], str]
    tool_start_time: Callable[[], float]
    now: Callable[[], float]
    agent_spacer_height: Callable[[], int]
    spinner_height: Callable[[], int]
    sudo_deadline: Callable[[], float]
    secret_deadline: Callable[[], float]
    approval_deadline: Callable[[], float]
    clarify_deadline: Callable[[], float]


class TuiDynamicTextRuntime:
    """Project current host state into prompt-toolkit text callbacks."""

    def __init__(self, ports: TuiDynamicTextPorts) -> None:
        self.ports = ports

    def placeholder(self) -> str:
        if self.ports.voice_recording():
            return "recording... Ctrl+B to stop"
        if self.ports.voice_processing():
            return "transcribing..."
        if self.ports.sudo_active():
            return "type password (hidden), Enter to skip"
        if self.ports.secret_active():
            return "type secret (hidden), Enter to skip"
        if self.ports.approval_active():
            return ""
        if self.ports.clarify_freetext():
            return "type your answer here and press Enter"
        if self.ports.clarify_active():
            return ""
        if self.ports.command_running():
            frame = self.ports.command_spinner_frame()
            status = self.ports.command_status() or "Processing command..."
            return f"{frame} {status}"
        if self.ports.agent_running():
            return "agent running... use /cancel to stop this turn"
        if self.ports.voice_mode():
            return "type or Ctrl+B to record"
        return ""

    def hint_fragments(self) -> list[tuple[str, str]]:
        if self.ports.sudo_active():
            return self._countdown_hint(
                "  password hidden · Enter to skip", self.ports.sudo_deadline()
            )
        if self.ports.secret_active():
            return self._countdown_hint(
                "  secret hidden · Enter to skip", self.ports.secret_deadline()
            )
        if self.ports.approval_active():
            return self._countdown_hint(
                "  ↑/↓ to select, Enter to confirm", self.ports.approval_deadline()
            )
        if self.ports.clarify_active():
            deadline = self.ports.clarify_deadline()
            countdown = f"  ({max(0, int(deadline - self.ports.now()))}s)" if deadline else ""
            if self.ports.clarify_freetext():
                return [
                    ("class:hint", "  type your answer and press Enter"),
                    ("class:clarify-countdown", countdown),
                ]
            return [
                ("class:hint", "  ↑/↓ to select, Enter to confirm"),
                ("class:clarify-countdown", countdown),
            ]
        if self.ports.command_running():
            frame = self.ports.command_spinner_frame()
            return [
                (
                    "class:hint",
                    f"  {frame} command in progress · input temporarily disabled",
                )
            ]
        return []

    def hint_height(self) -> int:
        if (
            self.ports.sudo_active()
            or self.ports.secret_active()
            or self.ports.approval_active()
            or self.ports.clarify_active()
            or self.ports.command_running()
        ):
            return 1
        return self.ports.agent_spacer_height()

    def spinner_fragments(self) -> list[tuple[str, str]]:
        text = self.ports.spinner_text()
        if not text:
            return []
        started_at = self.ports.tool_start_time()
        if started_at <= 0:
            return [("class:hint", f"  {text}")]
        elapsed = max(0.0, self.ports.now() - started_at)
        if elapsed >= 60:
            elapsed_label = f"{int(elapsed // 60)}m {int(elapsed % 60)}s"
        else:
            elapsed_label = f"{elapsed:.1f}s"
        return [("class:hint", f"  {text}  ({elapsed_label})")]

    def spinner_widget_height(self) -> int:
        return self.ports.spinner_height()

    def _countdown_hint(self, text: str, deadline: float) -> list[tuple[str, str]]:
        remaining = max(0, int(deadline - self.ports.now()))
        return [
            ("class:hint", text),
            ("class:clarify-countdown", f"  ({remaining}s)"),
        ]

"""Coordinate Ctrl+C and Ctrl+D for the terminal adapter."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class ControlKeybindingPorts:
    """State reads and host operations required by control-key handlers."""

    now: Callable[[], float]
    voice_recording: Callable[[], bool]
    cancel_voice_recording: Callable[[], None]
    interrupt_voice: Callable[[], None]
    run_background: Callable[[Callable[[], None]], None]
    sudo_active: Callable[[], bool]
    submit_sudo_cancel: Callable[[], None]
    clear_sudo_state: Callable[[], None]
    secret_active: Callable[[], bool]
    cancel_secret: Callable[[], None]
    clear_secret_input: Callable[[Any], None]
    approval_active: Callable[[], bool]
    deny_approval: Callable[[], None]
    clear_approval_state: Callable[[], None]
    model_picker_active: Callable[[], bool]
    close_model_picker: Callable[[], None]
    clear_model_picker_input: Callable[[Any], None]
    clarification_active: Callable[[], bool]
    cancel_clarification: Callable[[], None]
    clear_clarification_state: Callable[[], None]
    set_clarify_freetext: Callable[[bool], None]
    clear_clarification_input: Callable[[Any], None]
    agent_running: Callable[[], bool]
    interrupt_agent: Callable[[], None]
    set_last_ctrl_c_time: Callable[[float], None]
    has_input: Callable[[Any], bool]
    clear_input: Callable[[Any], None]
    autonomous_gate_active: Callable[[], bool]
    force_quit_autonomous: Callable[[], None]
    emit: Callable[[str], None]
    invalidate: Callable[[Any], None]


class ControlKeybindingRuntime:
    """Own Ctrl+C/D routing without accessing the CLI host directly."""

    def __init__(self, ports: ControlKeybindingPorts) -> None:
        self.ports = ports

    def handle_ctrl_c(self, event: Any) -> None:
        if self.ports.voice_recording():
            self.ports.cancel_voice_recording()
            self.ports.emit("\nRecording cancelled.")
            self.ports.run_background(self.ports.interrupt_voice)
            self.ports.invalidate(event)
            return

        if self._cancel_sudo(event):
            return
        if self._cancel_secret(event):
            return
        if self._cancel_approval(event):
            return
        if self._cancel_model_picker(event):
            return
        if self._cancel_clarification(event):
            return

        if self.ports.agent_running():
            self.ports.set_last_ctrl_c_time(self.ports.now())
            self.ports.interrupt_agent()
            return

        if self.ports.has_input(event):
            self.ports.clear_input(event)
            self.ports.invalidate(event)

    def handle_ctrl_d(self, event: Any) -> None:
        if self.ports.autonomous_gate_active():
            self.ports.clear_input(event)
            self.ports.emit("\n  ⚡ Ctrl+D — 触发紧急强制退出自主链路...")
            self.ports.force_quit_autonomous()
            self.ports.invalidate(event)
            return

        if self.ports.has_input(event):
            self.ports.clear_input(event)
            self.ports.invalidate(event)

    def _cancel_sudo(self, event: Any) -> bool:
        if not self.ports.sudo_active():
            return False
        self.ports.submit_sudo_cancel()
        self.ports.clear_sudo_state()
        self.ports.invalidate(event)
        return True

    def _cancel_secret(self, event: Any) -> bool:
        if not self.ports.secret_active():
            return False
        self.ports.cancel_secret()
        self.ports.clear_secret_input(event)
        self.ports.invalidate(event)
        return True

    def _cancel_approval(self, event: Any) -> bool:
        if not self.ports.approval_active():
            return False
        self.ports.deny_approval()
        self.ports.clear_approval_state()
        self.ports.invalidate(event)
        return True

    def _cancel_model_picker(self, event: Any) -> bool:
        if not self.ports.model_picker_active():
            return False
        self.ports.close_model_picker()
        self.ports.clear_model_picker_input(event)
        self.ports.invalidate(event)
        return True

    def _cancel_clarification(self, event: Any) -> bool:
        if not self.ports.clarification_active():
            return False
        self.ports.cancel_clarification()
        self.ports.clear_clarification_state()
        self.ports.set_clarify_freetext(False)
        self.ports.clear_clarification_input(event)
        self.ports.invalidate(event)
        return True

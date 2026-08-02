"""Route the Enter key across CLI modals, commands and turn queues."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from VoidCube_app.turn_queue import TurnInputRoute
from VoidCube_cli.command_router import looks_like_slash_command


@dataclass(frozen=True, slots=True)
class EnterKeybindingPorts:
    """UI state, command and queue operations supplied by the CLI host."""

    read_text: Callable[[Any], str]
    has_images: Callable[[], bool]
    snapshot_images: Callable[[], list[Any]]
    clear_images: Callable[[], None]
    reset_buffer: Callable[[Any, bool], None]
    invalidate: Callable[[Any], None]
    sudo_state: Callable[[], Mapping[str, Any] | None]
    set_sudo_state: Callable[[Mapping[str, Any] | None], None]
    submit_sudo: Callable[[str], None]
    secret_state: Callable[[], Any]
    submit_secret: Callable[[str], None]
    clear_secret_input: Callable[[Any], None]
    approval_state: Callable[[], Any]
    submit_approval: Callable[[], None]
    model_picker_state: Callable[[], Any]
    submit_model_picker: Callable[[], None]
    clarify_state: Callable[[], Mapping[str, Any] | None]
    set_clarify_state: Callable[[Mapping[str, Any] | None], None]
    clarify_freetext: Callable[[], bool]
    set_clarify_freetext: Callable[[bool], None]
    submit_clarification: Callable[[Any], None]
    restore_modal_input: Callable[[], None]
    clear_modal_states: Callable[[], None]
    status_bar_visible: Callable[[], bool]
    set_status_bar_visible: Callable[[bool], None]
    process_command: Callable[[str], bool]
    should_handle_model_inline: Callable[[str, bool], bool]
    set_should_exit: Callable[[bool], None]
    exit_application: Callable[[Any], None]
    stop_daemons: Callable[[bool], None]
    run_api_command: Callable[[Any], None]
    autonomous_gate_active: Callable[[], bool]
    exit_autonomous_gate_fast: Callable[[], None]
    enqueue_input: Callable[[Any, bool], TurnInputRoute]
    agent_running: Callable[[], bool]
    busy_input_mode: Callable[[], Any]
    emit: Callable[[str], None]


class EnterKeybindingRuntime:
    """Own Enter routing without directly accessing the CLI host."""

    def __init__(self, ports: EnterKeybindingPorts) -> None:
        self.ports = ports

    def handle(self, event: Any) -> None:
        if self._handle_modal_prompt(event):
            return

        text = self.ports.read_text(event).strip()
        has_images = self.ports.has_images()
        if text and text.startswith("/api"):
            self.ports.clear_modal_states()
            self.ports.restore_modal_input()
            was_visible = self.ports.status_bar_visible()
            self.ports.set_status_bar_visible(False)
            self.ports.invalidate(event)
            try:
                self.ports.run_api_command(event)
            finally:
                self.ports.set_status_bar_visible(was_visible)
                self.ports.invalidate(event)
            self.ports.reset_buffer(event, True)
            return

        if self.ports.model_picker_state():
            self.ports.submit_model_picker()
            self.ports.invalidate(event)
            return

        if self._handle_clarification(event):
            return

        if not text and not has_images:
            return
        if self._handle_inline_model_command(event, text, has_images):
            return
        if self._handle_quit(event, text):
            return

        images = self.ports.snapshot_images()
        self.ports.clear_images()
        self.ports.invalidate(event)
        payload = (text, images) if images else text

        if self.ports.autonomous_gate_active() and self._is_fast_autonomous_exit(text):
            self.ports.reset_buffer(event, True)
            self.ports.emit("  🔓 临时停用自主链路...")
            self.ports.exit_autonomous_gate_fast()
            self.ports.invalidate(event)
            return

        is_command = bool(text and looks_like_slash_command(text))
        route = self.ports.enqueue_input(payload, is_command)
        if route is TurnInputRoute.NEXT_TURN and self.ports.agent_running() and not is_command:
            preview = text or f"[{len(images)} image{'s' if len(images) != 1 else ''} attached]"
            self.ports.emit(
                f"  Queued for the next turn: {preview[:80]}"
                f"{'...' if len(preview) > 80 else ''}"
            )
        self.ports.reset_buffer(event, True)

    def _handle_modal_prompt(self, event: Any) -> bool:
        if self.ports.sudo_state():
            self.ports.submit_sudo(self.ports.read_text(event))
            self.ports.set_sudo_state(None)
            self.ports.invalidate(event)
            return True
        if self.ports.secret_state():
            self.ports.submit_secret(self.ports.read_text(event))
            self.ports.clear_secret_input(event)
            self.ports.invalidate(event)
            return True
        if self.ports.approval_state():
            self.ports.submit_approval()
            self.ports.invalidate(event)
            return True
        return False

    def _handle_clarification(self, event: Any) -> bool:
        state = self.ports.clarify_state()
        if not state:
            return False
        if self.ports.clarify_freetext():
            text = self.ports.read_text(event).strip()
            if text:
                self.ports.submit_clarification(text)
                self.ports.set_clarify_state(None)
                self.ports.set_clarify_freetext(False)
                self.ports.reset_buffer(event, False)
                self.ports.invalidate(event)
            return True

        selected = state["selected"]
        choices = state.get("choices") or []
        if selected < len(choices):
            self.ports.submit_clarification(choices[selected])
            self.ports.set_clarify_state(None)
            self.ports.invalidate(event)
        else:
            self.ports.set_clarify_freetext(True)
            self.ports.invalidate(event)
        return True

    def _handle_inline_model_command(self, event: Any, text: str, has_images: bool) -> bool:
        if not self.ports.should_handle_model_inline(text, has_images):
            return False
        if not self.ports.process_command(text):
            self.ports.set_should_exit(True)
            try:
                self.ports.exit_application(event)
            except Exception:
                pass
        self.ports.reset_buffer(event, True)
        return True

    def _handle_quit(self, event: Any, text: str) -> bool:
        if not text.startswith("/quit"):
            return False
        if not self.ports.process_command(text):
            self.ports.set_should_exit(True)
            self.ports.stop_daemons("--keep-daemons" in text)
            try:
                self.ports.exit_application(event)
            except Exception:
                pass
        self.ports.reset_buffer(event, True)
        return True

    def _is_fast_autonomous_exit(self, text: str) -> bool:
        if not text or not looks_like_slash_command(text):
            return False
        command = text.strip().lstrip("/").split()[0].lower()
        return command in ("auto-q", "auto-quit", "auto-stop")

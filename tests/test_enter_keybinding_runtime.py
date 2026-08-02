from __future__ import annotations

from types import SimpleNamespace

from VoidCube_app.turn_queue import TurnInputRoute
from VoidCube_cli.enter_keybinding_runtime import (
    EnterKeybindingPorts,
    EnterKeybindingRuntime,
)


class _Buffer:
    def __init__(self, text):
        self.text = text
        self.resets = []

    def reset(self, *, append_to_history=False):
        self.resets.append(append_to_history)
        self.text = ""


def _runtime(calls, state):
    return EnterKeybindingRuntime(
        EnterKeybindingPorts(
            read_text=lambda event: event.app.current_buffer.text,
            has_images=lambda: bool(state["images"]),
            snapshot_images=lambda: list(state["images"]),
            clear_images=lambda: state["images"].clear(),
            reset_buffer=lambda event, append: event.app.current_buffer.reset(
                append_to_history=append
            ),
            invalidate=lambda _event: calls.append("invalidate"),
            sudo_state=lambda: state["sudo"],
            set_sudo_state=lambda value: state.__setitem__("sudo", value),
            submit_sudo=lambda value: calls.append(("sudo", value)),
            secret_state=lambda: state["secret"],
            submit_secret=lambda value: calls.append(("secret", value)),
            clear_secret_input=lambda _event: calls.append("clear-secret"),
            approval_state=lambda: state["approval"],
            submit_approval=lambda: calls.append("approval"),
            model_picker_state=lambda: state["model"],
            submit_model_picker=lambda: calls.append("model"),
            clarify_state=lambda: state["clarify"],
            set_clarify_state=lambda value: state.__setitem__("clarify", value),
            clarify_freetext=lambda: state["clarify_text"],
            set_clarify_freetext=lambda value: state.__setitem__("clarify_text", value),
            submit_clarification=lambda value: calls.append(("clarify", value)),
            restore_modal_input=lambda: calls.append("restore-modal"),
            clear_modal_states=lambda: calls.append("clear-modals"),
            status_bar_visible=lambda: state["status_visible"],
            set_status_bar_visible=lambda value: state.__setitem__(
                "status_visible", value
            ),
            process_command=lambda value: calls.append(("command", value)) or True,
            should_handle_model_inline=lambda _text, _images: False,
            set_should_exit=lambda value: calls.append(("exit", value)),
            exit_application=lambda _event: calls.append("app-exit"),
            stop_daemons=lambda keep: calls.append(("stop-daemons", keep)),
            run_api_command=lambda _event: calls.append("api"),
            autonomous_gate_active=lambda: False,
            exit_autonomous_gate_fast=lambda: calls.append("auto-exit"),
            enqueue_input=lambda payload, is_command: calls.append(
                ("enqueue", payload, is_command)
            ) or TurnInputRoute.NEXT_TURN,
            agent_running=lambda: True,
            busy_input_mode=lambda: "interrupt",
            emit=lambda value: calls.append(("emit", value)),
        )
    )


def test_enter_keybinding_runtime_routes_normal_input_and_preserves_images():
    calls = []
    state = {
        "images": ["image.png"],
        "sudo": None,
        "secret": None,
        "approval": None,
        "model": None,
        "clarify": None,
        "clarify_text": False,
        "status_visible": True,
    }
    event = SimpleNamespace(app=SimpleNamespace(current_buffer=_Buffer(" hello ")))

    _runtime(calls, state).handle(event)

    assert ("enqueue", ("hello", ["image.png"]), False) in calls
    assert state["images"] == []
    assert event.app.current_buffer.resets == [True]
    assert any(call[0] == "emit" and "Queued" in call[1] for call in calls)


def test_enter_keybinding_runtime_submits_modal_text_without_normal_routing():
    calls = []
    state = {
        "images": [],
        "sudo": {"response_queue": object()},
        "secret": None,
        "approval": None,
        "model": None,
        "clarify": None,
        "clarify_text": False,
        "status_visible": True,
    }
    event = SimpleNamespace(app=SimpleNamespace(current_buffer=_Buffer(" raw ")))

    _runtime(calls, state).handle(event)

    assert ("sudo", " raw ") in calls
    assert state["sudo"] is None
    assert not any(call[0] == "enqueue" for call in calls if isinstance(call, tuple))

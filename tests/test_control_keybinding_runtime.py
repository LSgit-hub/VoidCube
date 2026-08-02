from __future__ import annotations

from types import SimpleNamespace

from VoidCube_cli.control_keybinding_runtime import (
    ControlKeybindingPorts,
    ControlKeybindingRuntime,
)


class _Buffer:
    def __init__(self, text: str) -> None:
        self.text = text
        self.resets = 0

    def reset(self) -> None:
        self.resets += 1
        self.text = ""


def _runtime(calls: list[object], state: dict[str, object]) -> ControlKeybindingRuntime:
    return ControlKeybindingRuntime(
        ControlKeybindingPorts(
            now=lambda: 12.5,
            voice_recording=lambda: bool(state["voice"]),
            cancel_voice_recording=lambda: calls.append("cancel-voice"),
            interrupt_voice=lambda: calls.append("interrupt-voice"),
            run_background=lambda operation: (calls.append("background"), operation()),
            sudo_active=lambda: bool(state["sudo"]),
            submit_sudo_cancel=lambda: calls.append("sudo-cancel"),
            clear_sudo_state=lambda: state.__setitem__("sudo", None),
            secret_active=lambda: bool(state["secret"]),
            cancel_secret=lambda: calls.append("secret-cancel"),
            clear_secret_input=lambda _event: calls.append("clear-secret"),
            approval_active=lambda: bool(state["approval"]),
            deny_approval=lambda: calls.append("deny-approval"),
            clear_approval_state=lambda: state.__setitem__("approval", None),
            model_picker_active=lambda: bool(state["model"]),
            close_model_picker=lambda: calls.append("close-model"),
            clear_model_picker_input=lambda _event: calls.append("clear-model-input"),
            clarification_active=lambda: bool(state["clarify"]),
            cancel_clarification=lambda: calls.append("cancel-clarify"),
            clear_clarification_state=lambda: state.__setitem__("clarify", None),
            set_clarify_freetext=lambda value: state.__setitem__("clarify-text", value),
            clear_clarification_input=lambda _event: calls.append("clear-clarify-input"),
            agent_running=lambda: bool(state["agent"]),
            interrupt_agent=lambda: calls.append("interrupt-agent"),
            set_last_ctrl_c_time=lambda value: calls.append(("last-ctrl-c", value)),
            has_input=lambda event: bool(event.app.current_buffer.text or state["images"]),
            clear_input=lambda event: (event.app.current_buffer.reset(), state["images"].clear()),
            autonomous_gate_active=lambda: bool(state["autonomous"]),
            force_quit_autonomous=lambda: calls.append("force-autonomous"),
            emit=lambda value: calls.append(("emit", value)),
            invalidate=lambda _event: calls.append("invalidate"),
        )
    )


def _event(text: str = ""):
    return SimpleNamespace(app=SimpleNamespace(current_buffer=_Buffer(text)))


def _state(**overrides: object) -> dict[str, object]:
    state = {
        "voice": False,
        "sudo": None,
        "secret": None,
        "approval": None,
        "model": None,
        "clarify": None,
        "clarify-text": True,
        "agent": False,
        "images": [],
        "autonomous": False,
    }
    state.update(overrides)
    return state


def test_ctrl_c_cancels_voice_without_touching_other_states() -> None:
    calls: list[object] = []
    state = _state(voice=True, sudo={"active": True})

    _runtime(calls, state).handle_ctrl_c(_event("typed"))

    assert calls[:4] == ["cancel-voice", ("emit", "\nRecording cancelled."), "background", "interrupt-voice"]
    assert "sudo-cancel" not in calls


def test_ctrl_c_cancels_modal_in_priority_order() -> None:
    calls: list[object] = []
    state = _state(approval={"active": True})
    event = _event("typed")

    _runtime(calls, state).handle_ctrl_c(event)

    assert calls == ["deny-approval", "invalidate"]
    assert state["approval"] is None
    assert event.app.current_buffer.text == "typed"


def test_ctrl_c_interrupts_agent_then_clears_idle_input() -> None:
    calls: list[object] = []
    state = _state(agent=True)
    _runtime(calls, state).handle_ctrl_c(_event("typed"))
    assert calls == [("last-ctrl-c", 12.5), "interrupt-agent"]

    calls.clear()
    state["agent"] = False
    state["images"] = ["image"]
    event = _event("typed")
    _runtime(calls, state).handle_ctrl_c(event)
    assert event.app.current_buffer.resets == 1
    assert state["images"] == []
    assert calls == ["invalidate"]


def test_ctrl_d_force_quits_autonomous_chain_or_clears_input() -> None:
    calls: list[object] = []
    state = _state(autonomous=True)
    event = _event("typed")
    _runtime(calls, state).handle_ctrl_d(event)
    assert "force-autonomous" in calls
    assert event.app.current_buffer.resets == 1

    calls.clear()
    state["autonomous"] = False
    event = _event("typed")
    _runtime(calls, state).handle_ctrl_d(event)
    assert calls == ["invalidate"]
    assert event.app.current_buffer.resets == 1

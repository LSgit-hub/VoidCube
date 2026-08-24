from types import SimpleNamespace

from voidcube.interfaces.cli.voice_keybinding_runtime import (
    VoiceKeybindingPorts,
    VoiceKeybindingRuntime,
)


def _runtime(calls, state, start_recording=None):
    return VoiceKeybindingRuntime(
        VoiceKeybindingPorts(
            voice_mode=lambda: state["mode"],
            recording=lambda: state["recording"],
            set_continuous=lambda value: calls.append(("continuous", value)),
            agent_running=lambda: state["agent"],
            modal_active=lambda: state["modal"],
            processing=lambda: state["processing"],
            start_recording=start_recording or (lambda: calls.append("start")),
            stop_recording=lambda: calls.append("stop"),
            run_background=lambda operation: (calls.append("background"), operation()),
            invalidate=lambda _event: calls.append("invalidate"),
            invalidate_app=lambda: calls.append("invalidate-app"),
            report_error=lambda error: calls.append(("error", str(error))),
        )
    )


def _event():
    return SimpleNamespace()


def test_voice_key_starts_and_stops_only_outside_busy_states():
    state = {"mode": True, "recording": False, "agent": False, "modal": False, "processing": False}
    calls = []
    runtime = _runtime(calls, state)

    runtime.handle(_event())
    assert calls == [("continuous", True), "background", "start", "invalidate-app", "invalidate"]

    calls.clear()
    state["recording"] = True
    runtime.handle(_event())
    assert calls == [("continuous", False), "invalidate", "background", "stop"]

    calls.clear()
    state["recording"] = False
    state["agent"] = True
    runtime.handle(_event())
    assert calls == []


def test_voice_key_reports_start_failure_without_leaking_to_event_loop():
    calls = []
    state = {"mode": True, "recording": False, "agent": False, "modal": False, "processing": False}
    ports_runtime = _runtime(
        calls,
        state,
        start_recording=lambda: (_ for _ in ()).throw(RuntimeError("bad capture")),
    )

    ports_runtime.handle(_event())

    assert ("error", "bad capture") in calls

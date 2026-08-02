from __future__ import annotations

from VoidCube_cli.voice_recording_runtime import (
    VoiceRecordingPorts,
    start_terminal_voice_recording,
    stop_terminal_voice_recording,
)
from VoidCube_cli.voice_runtime_state import CliVoiceRuntimeState


def make_ports(state: CliVoiceRuntimeState, *, should_exit=lambda: False):
    calls: list[tuple[str, object]] = []
    return (
        VoiceRecordingPorts(
            state=state,
            should_exit=should_exit,
            invalidate=lambda: calls.append(("invalidate", None)),
            emit=lambda message: calls.append(("emit", message)),
            enqueue_input=lambda text: calls.append(("enqueue", text)),
            clear_attached_images=lambda: calls.append(("clear_images", None)),
        ),
        calls,
    )


def test_start_terminal_voice_recording_does_not_touch_audio_after_exit() -> None:
    state = CliVoiceRuntimeState()
    ports, calls = make_ports(state, should_exit=lambda: True)

    start_terminal_voice_recording(ports)

    assert state.recording is False
    assert calls == []


def test_stop_terminal_voice_recording_interrupts_canonical_session() -> None:
    state = CliVoiceRuntimeState(continuous=True)
    state.recording = True
    ports, calls = make_ports(state)

    stop_terminal_voice_recording(ports)

    assert state.recording is False
    assert state.processing is False
    assert state.continuous is False
    assert ("emit", "\nRecording cancelled.") in calls

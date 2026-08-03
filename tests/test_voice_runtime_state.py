from VoidCube_cli.voice_runtime_state import CliVoiceRuntimeState


def test_voice_runtime_state_is_initialized_for_idle_voice_session() -> None:
    state = CliVoiceRuntimeState()

    assert state.mode is False
    assert state.recording is False
    assert state.processing is False
    assert state.continuous is False
    assert state.no_speech_count == 0
    assert state.stop_continuous is False


def test_voice_runtime_instances_do_not_share_locks_or_events() -> None:
    first = CliVoiceRuntimeState()
    second = CliVoiceRuntimeState()

    first.stop_continuous = True

    assert first.lock is not second.lock
    assert first.stop_continuous is True
    assert second.stop_continuous is False

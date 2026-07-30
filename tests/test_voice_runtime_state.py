from VoidCube_cli.voice_runtime_state import CliVoiceRuntimeState


def test_voice_runtime_state_is_initialized_for_idle_voice_session() -> None:
    state = CliVoiceRuntimeState()

    assert state.mode is False
    assert state.recording is False
    assert state.processing is False
    assert state.continuous is False
    assert state.recorder is None
    assert state.tts_done.is_set()


def test_voice_runtime_instances_do_not_share_locks_or_events() -> None:
    first = CliVoiceRuntimeState()
    second = CliVoiceRuntimeState()

    first.tts_done.clear()

    assert first.lock is not second.lock
    assert first.tts_done is not second.tts_done
    assert second.tts_done.is_set()

from VoidCube_cli.cli_voice_status_runtime import CliVoiceStatusPorts, CliVoiceStatusRuntime


def _runtime(*, width=80, compact=False, recording=False, processing=False, continuous=False):
    return CliVoiceStatusRuntime(
        CliVoiceStatusPorts(
            terminal_width=lambda: width,
            minimal_chrome=lambda _width: compact,
            recording=lambda: recording,
            processing=lambda: processing,
            continuous=lambda: continuous,
        )
    )


def test_voice_status_prefers_recording_and_processing_states():
    assert "REC" in "".join(text for _, text in _runtime(recording=True).build())
    assert "Transcribing" in "".join(
        text for _, text in _runtime(processing=True).build()
    )


def test_voice_status_compacts_and_marks_continuous_mode():
    compact = "".join(text for _, text in _runtime(compact=True).build())
    full = "".join(text for _, text in _runtime(continuous=True).build())

    assert compact == " 🎤 Ctrl+B "
    assert "Voice mode | Continuous" in full

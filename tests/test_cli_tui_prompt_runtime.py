from VoidCube_cli.cli_tui_prompt_runtime import (
    CliTuiPromptPorts,
    CliTuiPromptRuntime,
)


def _runtime(**overrides):
    values = {
        "voice_recording": lambda: False,
        "voice_processing": lambda: False,
        "sudo_active": lambda: False,
        "secret_active": lambda: False,
        "approval_active": lambda: False,
        "clarify_freetext": lambda: False,
        "clarify_active": lambda: False,
        "command_running": lambda: False,
        "command_spinner_frame": lambda: "|",
        "agent_running": lambda: False,
        "voice_mode": lambda: False,
        "minimal_tui_chrome": lambda width: False,
        "terminal_width": lambda: 80,
        "audio_status": lambda: {"audio_rms": 0.5},
    }
    values.update(overrides)
    return CliTuiPromptRuntime(CliTuiPromptPorts(**values))


def test_prompt_runtime_preserves_state_priority_and_compact_rendering():
    runtime = _runtime(
        voice_recording=lambda: True,
        voice_processing=lambda: True,
        minimal_tui_chrome=lambda _width: True,
        audio_status=lambda: {"audio_rms": 1.0},
    )

    assert runtime.fragments() == [("class:voice-recording", "● ▇ ")]
    assert runtime.text() == "● ▇ "


def test_prompt_runtime_builds_profile_prompt_and_handles_audio_failures():
    runtime = _runtime(audio_status=lambda: (_ for _ in ()).throw(RuntimeError("offline")))

    assert runtime.audio_level_bar() == ""
    assert runtime.prompt_symbols() == ("❯ ", "❯ ")

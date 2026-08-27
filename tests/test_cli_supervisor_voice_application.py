from types import SimpleNamespace

from voidcube.interfaces.cli.application import VoidcubeCLI
from voidcube.interfaces.cli.voice_runtime_state import CliVoiceRuntimeState


class _FakeSupervisorVoiceClient:
    def __init__(self):
        self.calls = []

    def status(self):
        self.calls.append("status")
        if self.calls.count("status") == 1:
            return {"enabled": False, "capture_available": True, "stt_configured": True}
        return {"enabled": True, "active": False, "capture_available": True, "stt_configured": True}

    def set_microphone(self, enabled):
        self.calls.append(("mic", enabled))
        return {"enabled": enabled, "capture_available": True, "stt_configured": True}

    def start_session(self, *, session_id=""):
        self.calls.append(("session", session_id))
        return {"status": "complete", "transcript": "hi", "reply_text": "hello"}


def _app():
    app = VoidcubeCLI.__new__(VoidcubeCLI)
    app._voice_runtime_state = CliVoiceRuntimeState(target="supervisor")
    app._should_exit = False
    app._app = SimpleNamespace(invalidate=lambda: None)
    app.session_id = "session-1"
    return app


def test_cli_supervisor_voice_session_enables_mic_and_refreshes_status(monkeypatch):
    messages = []
    client = _FakeSupervisorVoiceClient()
    app = _app()
    app._supervisor_voice_client_instance = client
    monkeypatch.setattr(
        "voidcube.interfaces.cli.application._cprint",
        messages.append,
    )

    app._start_supervisor_voice_session()

    assert client.calls == [
        "status",
        ("mic", True),
        ("session", "session-1"),
        "status",
    ]
    assert app._voice_state().recording is False
    assert app._supervisor_state_cache["voice"]["enabled"] is True


def test_cli_supervisor_realtime_status_reads_cached_supervisor_voice():
    app = _app()
    app._supervisor_state_cache = {
        "scene": "idle",
        "voice": {"enabled": True, "active": True},
    }
    app._voice_session = lambda: (_ for _ in ()).throw(AssertionError("local voice used"))

    assert app._voice_realtime_status() == {"enabled": True, "active": True}


def test_supervisor_voice_target_does_not_mark_normal_api_a_input_as_voice():
    app = _app()
    app._voice_runtime_state.mode = True

    assert app._voice_input_prefix("normal CLI input", owner=app) == ""


def test_terminal_voice_target_marks_local_api_a_voice_input():
    app = _app()
    app._voice_runtime_state.target = "terminal"
    app._voice_runtime_state.mode = True

    assert "Voice input" in app._voice_input_prefix("spoken input", owner=app)

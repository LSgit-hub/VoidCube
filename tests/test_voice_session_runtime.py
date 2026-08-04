import asyncio

from VoidCube_app.voice_session_runtime import VoiceSessionRuntime


class FakeVoiceManager:
    def __init__(self) -> None:
        self.interrupted = False
        self.spoken: list[tuple[str, str]] = []
        self.transcribed = False

    def status(self) -> dict[str, object]:
        return {
            "enabled": True,
            "tts_configured": True,
            "playback_available": True,
        }

    async def speak_text(self, text: str, *, reason: str) -> dict[str, str]:
        await asyncio.sleep(0)
        self.spoken.append((text, reason))
        return {"status": "complete", "reply_text": text, "reason": reason}

    def interrupt(self) -> dict[str, str]:
        self.interrupted = True
        return {"status": "interrupted"}

    def set_enabled(self, enabled: bool) -> dict[str, object]:
        return {
            "enabled": enabled,
            "tts_configured": True,
            "playback_available": True,
            "capture_available": True,
            "stt_configured": True,
        }

    async def transcribe_once(self, *, session_id: str) -> dict[str, str]:
        self.transcribed = True
        return {"status": "complete", "transcript": f"{session_id}: transcript"}

    def realtime_status(self) -> dict[str, float]:
        return {"audio_rms": 0.25}


def test_voice_session_runtime_keeps_manager_on_one_loop() -> None:
    managers: list[FakeVoiceManager] = []

    def create_manager() -> FakeVoiceManager:
        manager = FakeVoiceManager()
        managers.append(manager)
        return manager

    runtime = VoiceSessionRuntime(manager_factory=create_manager)
    try:
        assert runtime.status()["status"] == "available"
        assert runtime.speak("hello", reason="test")["status"] == "complete"
        assert managers[0].spoken == [("hello", "test")]
        assert runtime.interrupt()["status"] == "interrupted"
        assert managers[0].interrupted is True
    finally:
        runtime.close()


def test_voice_session_runtime_reports_disabled_output_without_success() -> None:
    class DisabledManager(FakeVoiceManager):
        def status(self) -> dict[str, object]:
            return {
                "enabled": False,
                "tts_configured": True,
                "playback_available": True,
            }

        async def speak_text(self, text: str, *, reason: str) -> dict[str, str]:
            return {"status": "disabled", "reason": "voice_disabled"}

    runtime = VoiceSessionRuntime(manager_factory=DisabledManager)
    try:
        assert runtime.status() == {
            "status": "unavailable",
            "reason": "voice_output_disabled",
            "voice": {
                "enabled": False,
                "tts_configured": True,
                "playback_available": True,
            },
        }
        assert runtime.speak("disabled")["status"] == "disabled"
    finally:
        runtime.close()


def test_voice_session_runtime_maps_capture_to_the_same_manager() -> None:
    manager = FakeVoiceManager()
    runtime = VoiceSessionRuntime(manager_factory=lambda: manager)
    try:
        enabled = runtime.enable()
        result = runtime.transcribe_once(session_id="terminal")

        assert enabled["stt_configured"] is True
        assert result == {
            "status": "complete",
            "transcript": "terminal: transcript",
        }
        assert runtime.realtime_status() == {"audio_rms": 0.25}
        assert manager.transcribed is True
        runtime.disable()
    finally:
        runtime.close()

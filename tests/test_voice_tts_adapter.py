import asyncio

from VoidCube_cli.voice_tts_adapter import VoiceTtsAdapter


class FakeVoiceManager:
    def __init__(self) -> None:
        self.interrupted = False
        self.spoken: list[tuple[str, str]] = []

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


def test_voice_tts_adapter_keeps_async_manager_on_one_loop() -> None:
    managers: list[FakeVoiceManager] = []

    def create_manager() -> FakeVoiceManager:
        manager = FakeVoiceManager()
        managers.append(manager)
        return manager

    adapter = VoiceTtsAdapter(manager_factory=create_manager)
    try:
        assert adapter.status()["status"] == "available"
        assert adapter.speak("你好", reason="test")["status"] == "complete"
        assert managers[0].spoken == [("你好", "test")]
        assert adapter.interrupt()["status"] == "interrupted"
        assert managers[0].interrupted is True
    finally:
        adapter.close()


def test_voice_tts_adapter_reports_disabled_output_without_success() -> None:
    class DisabledManager(FakeVoiceManager):
        def status(self) -> dict[str, object]:
            return {
                "enabled": False,
                "tts_configured": True,
                "playback_available": True,
            }

        async def speak_text(self, text: str, *, reason: str) -> dict[str, str]:
            return {"status": "disabled", "reason": "voice_disabled"}

    adapter = VoiceTtsAdapter(manager_factory=DisabledManager)
    try:
        assert adapter.status() == {
            "status": "unavailable",
            "reason": "voice_output_disabled",
            "voice": {
                "enabled": False,
                "tts_configured": True,
                "playback_available": True,
            },
        }
        assert adapter.speak("不会播放")["status"] == "disabled"
    finally:
        adapter.close()

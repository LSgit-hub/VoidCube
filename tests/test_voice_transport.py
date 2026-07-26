from __future__ import annotations

import asyncio
from pathlib import Path
import struct
import wave

import pytest

from systems.voice.config import VoiceConfig
from systems.voice.fingerprint import FingerprintStore, extract_voice_template
from systems.voice.session import VoiceSessionManager


def _wav(path: Path, *, frequency: float = 220.0) -> Path:
    sample_rate = 8000
    frames = []
    for index in range(sample_rate // 2):
        value = int(12000 * __import__("math").sin(2 * __import__("math").pi * frequency * index / sample_rate))
        frames.append(struct.pack("<h", value))
    with wave.open(str(path), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(sample_rate)
        output.writeframes(b"".join(frames))
    return path


@pytest.mark.unit
def test_fingerprint_store_persists_derived_template_only(tmp_path):
    audio = _wav(tmp_path / "input.wav")
    fingerprint_path = tmp_path / "fingerprint.json"
    store = FingerprintStore(fingerprint_path, threshold=0.8)

    enrolled = store.enroll(audio)
    verified = store.verify(audio)

    assert enrolled["status"] == "enrolled"
    assert verified["authenticated"] is True
    assert "template" in fingerprint_path.read_text(encoding="utf-8")
    assert "input.wav" not in fingerprint_path.read_text(encoding="utf-8")


@pytest.mark.unit
def test_fingerprint_mismatch_is_rejected(tmp_path):
    first = _wav(tmp_path / "first.wav", frequency=220)
    second = _wav(tmp_path / "second.wav", frequency=880)
    store = FingerprintStore(tmp_path / "fingerprint.json", threshold=0.999)
    store.enroll(first)

    result = store.verify(second)

    assert result["authenticated"] is False
    assert result["reason"] == "voice_fingerprint_mismatch"


@pytest.mark.asyncio
@pytest.mark.unit
async def test_voice_session_rejects_when_microphone_is_disabled(tmp_path):
    manager = VoiceSessionManager(
        VoiceConfig(enabled=False, fingerprint_path=tmp_path / "fingerprint.json")
    )

    result = await manager.run_once()

    assert result == {"status": "disabled", "reason": "voice_disabled"}


@pytest.mark.asyncio
@pytest.mark.unit
async def test_voice_session_authenticates_transcribes_calls_companion_and_cleans_audio(tmp_path):
    config = VoiceConfig(
        enabled=True,
        fingerprint_path=tmp_path / "fingerprint.json",
        retain_raw_audio=False,
    )
    manager = VoiceSessionManager(config, companion_callback=companion)
    manager.recorder.record = fake_record  # type: ignore[method-assign]
    manager.fingerprint.verify = lambda path: {"authenticated": True, "similarity": 1.0}  # type: ignore[method-assign]
    manager.stt.transcribe = fake_transcribe  # type: ignore[method-assign]
    manager.tts.synthesize = fake_synthesize  # type: ignore[method-assign]
    manager.player.play = fake_play  # type: ignore[method-assign]
    manager._temporary_audio_path = (  # type: ignore[method-assign]
        lambda prefix, suffix=".wav": tmp_path / f"{prefix}{suffix}"
    )

    result = await manager.run_once(session_id="voice-test")

    assert result["status"] == "complete"
    assert result["transcript"] == "当前任务是什么"
    assert result["reply_text"] == "当前正在处理记忆隔离。"
    assert manager.status()["active"] is False
    assert not (tmp_path / "input.wav").exists()
    assert not (tmp_path / "reply.mp3").exists()


@pytest.mark.asyncio
@pytest.mark.unit
async def test_continuous_voice_discards_non_wake_segments_and_stops_cleanly(tmp_path):
    config = VoiceConfig(
        enabled=True,
        fingerprint_path=tmp_path / "fingerprint.json",
        retain_raw_audio=False,
        continuous_segment_seconds=0.5,
        wake_word="星子",
        wake_word_required=True,
        wake_window_seconds=2.0,
    )
    callbacks: list[str] = []
    transcriptions = iter(["只是背景声音", "星子", "请告诉我当前任务"])
    replied = asyncio.Event()

    async def companion_for_continuous(*, text: str, session_id: str):
        callbacks.append(text)
        replied.set()
        return {"status": "ok", "reply_text": "当前正在处理记忆隔离。"}

    manager = VoiceSessionManager(config, companion_callback=companion_for_continuous)
    manager.recorder.record = fake_record  # type: ignore[method-assign]
    manager.fingerprint.verify = lambda path: {"authenticated": True, "similarity": 1.0}  # type: ignore[method-assign]

    async def transcribe_sequence(path):
        try:
            return next(transcriptions)
        except StopIteration:
            await asyncio.sleep(10)
            return ""

    manager.stt.transcribe = transcribe_sequence  # type: ignore[method-assign]
    manager.tts.synthesize = fake_synthesize  # type: ignore[method-assign]
    manager.player.play = fake_play  # type: ignore[method-assign]
    manager._temporary_audio_path = (  # type: ignore[method-assign]
        lambda prefix, suffix=".wav": tmp_path / f"{prefix}-{len(callbacks)}{suffix}"
    )

    started = manager.start_continuous(session_id="continuous-test")
    assert started["status"] == "started"
    await asyncio.wait_for(replied.wait(), timeout=2)

    stopped = await manager.stop_continuous()

    assert callbacks == ["请告诉我当前任务"]
    assert stopped["status"] == "stopped"
    assert stopped["continuous_active"] is False
    assert stopped["wake_state"] == "idle"
    assert stopped["wake_word_hits"] == 1
    assert stopped["last_transcript"] == "请告诉我当前任务"
    assert "背景" not in stopped["last_transcript"]
    assert not list(tmp_path.glob("*.wav"))
    assert not list(tmp_path.glob("*.mp3"))


@pytest.mark.asyncio
@pytest.mark.unit
async def test_voice_speak_text_plays_authorized_reminder_without_microphone(tmp_path):
    manager = VoiceSessionManager(
        VoiceConfig(enabled=True, fingerprint_path=tmp_path / "fingerprint.json")
    )
    manager.tts.synthesize = fake_synthesize  # type: ignore[method-assign]
    manager.player.play = fake_play  # type: ignore[method-assign]
    manager._temporary_audio_path = (  # type: ignore[method-assign]
        lambda prefix, suffix=".wav": tmp_path / f"{prefix}{suffix}"
    )

    result = await manager.speak_text("请检查当前任务。", reason="proactive_test")

    assert result == {
        "status": "complete",
        "reply_text": "请检查当前任务。",
        "reason": "proactive_test",
    }
    assert manager.status()["last_reply"] == "请检查当前任务。"
    assert not (tmp_path / "proactive.mp3").exists()


async def companion(*, text: str, session_id: str):
    assert text == "当前任务是什么"
    assert session_id == "voice-test"
    return {"status": "ok", "reply_text": "当前正在处理记忆隔离。"}


async def fake_record(path, *, duration_seconds, stop_event):
    _wav(Path(path))
    return Path(path)


async def fake_transcribe(path):
    return "当前任务是什么"


async def fake_synthesize(text, path):
    Path(path).write_bytes(b"fake-audio")
    return Path(path)


async def fake_play(path, *, stop_event):
    assert Path(path).is_file()

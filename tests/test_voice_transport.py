from __future__ import annotations

import asyncio
from pathlib import Path
import struct
import sys
from types import SimpleNamespace
import wave
from unittest.mock import AsyncMock, Mock

import pytest

from systems.voice.audio import AudioPlayer, AudioRecorder
from systems.voice.config import VoiceConfig
from systems.voice.fingerprint import FingerprintStore, SPEAKER_ENGINE
from systems.voice.session import VoiceSessionManager
from systems.voice.stt import SpeechToText


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
    audio_paths = [
        _wav(tmp_path / f"input-{index}.wav", frequency=220 + index)
        for index in range(3)
    ]
    fingerprint_path = tmp_path / "fingerprint.json"
    store = FingerprintStore(fingerprint_path, threshold=0.5)
    embeddings = iter(
        [
            [1.0, 0.0, 0.0],
            [0.99, 0.01, 0.0],
            [0.98, 0.02, 0.0],
            [1.0, 0.0, 0.0],
        ]
    )
    store._extract_embedding = lambda path: next(embeddings)  # type: ignore[method-assign]

    recorded = store.record_owner_templates(audio_paths)
    verified = store.verify(audio_paths[0])
    payload = fingerprint_path.read_text(encoding="utf-8")

    assert recorded["status"] == "owner_voice_template_recorded"
    assert recorded["enrollment_sample_count"] == 3
    assert recorded["embedding_dimension"] == 3
    assert verified["owner_voice_matched"] is True
    assert f'"engine": "{SPEAKER_ENGINE}"' in payload
    assert '"embeddings"' in payload
    assert "input-0.wav" not in payload


@pytest.mark.unit
def test_fingerprint_mismatch_is_rejected(tmp_path):
    paths = [_wav(tmp_path / f"owner-{index}.wav") for index in range(3)]
    second = _wav(tmp_path / "different.wav", frequency=880)
    store = FingerprintStore(tmp_path / "fingerprint.json", threshold=0.5)
    embeddings = iter(
        [
            [1.0, 0.0, 0.0],
            [0.99, 0.01, 0.0],
            [0.98, 0.02, 0.0],
            [0.0, 1.0, 0.0],
        ]
    )
    store._extract_embedding = lambda path: next(embeddings)  # type: ignore[method-assign]
    store.record_owner_templates(paths)

    result = store.verify(second)

    assert result["owner_voice_matched"] is False
    assert result["reason"] == "owner_voice_mismatch"


@pytest.mark.unit
def test_legacy_fingerprint_template_requires_explicit_reenrollment(tmp_path):
    fingerprint_path = tmp_path / "fingerprint.json"
    fingerprint_path.write_text(
        '{"version": 1, "template": [1.0, 0.0]}',
        encoding="utf-8",
    )
    store = FingerprintStore(fingerprint_path)

    result = store.verify(_wav(tmp_path / "input.wav"))

    assert store.template_status == "upgrade_required"
    assert result["owner_voice_matched"] is False
    assert result["reason"] == "owner_voice_template_upgrade_required"


@pytest.mark.unit
def test_audio_availability_requires_real_input_and_output_devices(monkeypatch):
    fake_sounddevice = SimpleNamespace(
        query_devices=lambda *, kind: {
            "max_input_channels": 1 if kind == "input" else 0,
            "max_output_channels": 1 if kind == "output" else 0,
        }
    )
    monkeypatch.setitem(sys.modules, "sounddevice", fake_sounddevice)
    monkeypatch.setitem(sys.modules, "soundfile", SimpleNamespace())
    monkeypatch.setitem(sys.modules, "numpy", SimpleNamespace())

    assert AudioRecorder.available() is True
    assert AudioPlayer.available() is True


@pytest.mark.asyncio
@pytest.mark.unit
async def test_audio_player_prefers_in_process_sounddevice_backend(tmp_path, monkeypatch):
    played = {}
    stopped = []
    stream = SimpleNamespace(active=False)
    fake_sounddevice = SimpleNamespace(
        query_devices=lambda *, kind: {"max_output_channels": 2},
        play=lambda audio, *, samplerate, blocking: played.update(
            audio=audio,
            samplerate=samplerate,
            blocking=blocking,
        ),
        get_stream=lambda: stream,
        stop=lambda: stopped.append(True),
    )
    fake_soundfile = SimpleNamespace(
        read=lambda path, **kwargs: ("decoded-audio", 24000),
    )
    monkeypatch.setitem(sys.modules, "sounddevice", fake_sounddevice)
    monkeypatch.setitem(sys.modules, "soundfile", fake_soundfile)
    monkeypatch.setattr("systems.voice.audio.shutil.which", lambda name: None)
    audio_path = tmp_path / "reply.mp3"
    audio_path.write_bytes(b"encoded-audio")

    player = AudioPlayer()
    await player.play(audio_path, stop_event=asyncio.Event())

    assert played == {
        "audio": "decoded-audio",
        "samplerate": 24000,
        "blocking": False,
    }
    assert stopped == []


@pytest.mark.asyncio
@pytest.mark.unit
async def test_local_stt_lazily_loads_faster_whisper(tmp_path, monkeypatch):
    created = []

    class FakeModel:
        def __init__(self, model, *, device, compute_type):
            created.append((model, device, compute_type))

        def transcribe(self, path, **kwargs):
            assert kwargs["hotwords"] == "星子 西子 VoidCube 语音系统"
            return iter([SimpleNamespace(text=" 本地转写成功。")]), SimpleNamespace()

    monkeypatch.setitem(
        sys.modules,
        "faster_whisper",
        SimpleNamespace(WhisperModel=FakeModel),
    )
    stt = SpeechToText(
        provider="local",
        base_url="",
        api_key="",
        model="base",
        language="zh",
        hotwords="星子 西子 VoidCube 语音系统",
        device="cpu",
        compute_type="int8",
    )
    audio_path = tmp_path / "input.wav"
    audio_path.write_bytes(b"audio")

    first = await stt.transcribe(audio_path)
    second = await stt.transcribe(audio_path)

    assert first == "本地转写成功。"
    assert second == first
    assert created == [("base", "cpu", "int8")]


@pytest.mark.unit
def test_stt_auto_provider_prefers_configured_remote_endpoint():
    stt = SpeechToText(
        provider="auto",
        base_url="https://voice.example/v1",
        api_key="secret",
        model="whisper-1",
    )

    assert stt.provider == "remote"
    assert stt.configured is True


@pytest.mark.unit
def test_voice_status_exposes_owner_filter_not_user_authentication(tmp_path):
    manager = VoiceSessionManager(
        VoiceConfig(enabled=False, fingerprint_path=tmp_path / "fingerprint.json")
    )

    status = manager.status()

    assert status["owner_voice_matched"] is False
    assert status["owner_voice_template_present"] is False
    assert status["fingerprint_enabled"] is True
    assert status["fingerprint_status"] == "missing"
    assert status["last_fingerprint_reason"] == "not_checked"
    assert "authenticated" not in status
    assert "fingerprint_enrolled" not in status
    assert manager.interrupt()["interrupted"] is False


@pytest.mark.asyncio
@pytest.mark.unit
async def test_disabled_fingerprint_filter_skips_verification_and_accepts_speaker(tmp_path):
    manager = VoiceSessionManager(
        VoiceConfig(
            enabled=True,
            fingerprint_enabled=False,
            fingerprint_path=tmp_path / "fingerprint.json",
        ),
        companion_callback=companion,
    )
    manager.recorder.record = fake_record  # type: ignore[method-assign]
    manager.fingerprint.verify = Mock()  # type: ignore[method-assign]
    manager.stt.transcribe = fake_transcribe  # type: ignore[method-assign]
    manager.tts.synthesize = fake_synthesize  # type: ignore[method-assign]
    manager.player.play = fake_play  # type: ignore[method-assign]
    manager._temporary_audio_path = (  # type: ignore[method-assign]
        lambda prefix, suffix=".wav": tmp_path / f"{prefix}{suffix}"
    )

    result = await manager.run_once(session_id="voice-test")

    assert result["status"] == "complete"
    assert result["voice_match"] == {
        "owner_voice_matched": False,
        "verification_skipped": True,
        "reason": "fingerprint_disabled",
    }
    manager.fingerprint.verify.assert_not_called()
    assert manager.status()["fingerprint_status"] == "disabled"
    assert manager.status()["last_fingerprint_reason"] == "fingerprint_disabled"


@pytest.mark.asyncio
@pytest.mark.unit
async def test_fingerprint_rejection_exposes_similarity_and_threshold(tmp_path):
    manager = VoiceSessionManager(
        VoiceConfig(
            enabled=True,
            fingerprint_enabled=True,
            fingerprint_path=tmp_path / "fingerprint.json",
        )
    )
    manager.recorder.record = fake_record  # type: ignore[method-assign]
    manager.fingerprint.verify = lambda path: {  # type: ignore[method-assign]
        "owner_voice_matched": False,
        "reason": "owner_voice_mismatch",
        "similarity": 0.71,
        "threshold": 0.5,
    }
    manager._temporary_audio_path = (  # type: ignore[method-assign]
        lambda prefix, suffix=".wav": tmp_path / f"{prefix}{suffix}"
    )

    result = await manager.run_once(session_id="voice-rejected")
    status = manager.status()

    assert result["status"] == "rejected"
    assert status["last_status"] == "rejected"
    assert status["owner_voice_matched"] is False
    assert status["last_fingerprint_reason"] == "owner_voice_mismatch"
    assert status["last_fingerprint_similarity"] == 0.71
    assert status["fingerprint_threshold"] == 0.5


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
async def test_owner_template_rejects_when_microphone_is_disabled(tmp_path):
    manager = VoiceSessionManager(
        VoiceConfig(enabled=False, fingerprint_path=tmp_path / "fingerprint.json")
    )
    manager.recorder.record = AsyncMock()  # type: ignore[method-assign]

    result = await manager.record_owner_template()

    assert result == {"status": "disabled", "reason": "voice_disabled"}
    manager.recorder.record.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.unit
async def test_owner_template_records_three_segments_and_cleans_audio(tmp_path):
    manager = VoiceSessionManager(
        VoiceConfig(enabled=True, fingerprint_path=tmp_path / "fingerprint.json")
    )
    manager.recorder.record = AsyncMock(side_effect=fake_record)  # type: ignore[method-assign]
    manager.fingerprint.record_owner_templates = Mock(  # type: ignore[method-assign]
        return_value={
            "status": "owner_voice_template_recorded",
            "enrollment_sample_count": 3,
            "embedding_dimension": 192,
        }
    )
    manager._temporary_audio_path = (  # type: ignore[method-assign]
        lambda prefix, suffix=".wav": tmp_path / f"{prefix}{suffix}"
    )

    result = await manager.record_owner_template(duration_seconds=3, sample_count=3)

    assert result["status"] == "owner_voice_template_recorded"
    assert result["enrollment_sample_count"] == 3
    assert manager.recorder.record.await_count == 3
    assert manager.status()["enrollment_sample_index"] == 3
    assert not list(tmp_path.glob("owner-template-*.wav"))


@pytest.mark.asyncio
@pytest.mark.unit
async def test_voice_session_matches_owner_transcribes_calls_companion_and_cleans_audio(tmp_path):
    config = VoiceConfig(
        enabled=True,
        fingerprint_path=tmp_path / "fingerprint.json",
        retain_raw_audio=False,
    )
    manager = VoiceSessionManager(config, companion_callback=companion)
    manager.recorder.record = fake_record  # type: ignore[method-assign]
    manager.fingerprint.verify = lambda path: {"owner_voice_matched": True, "similarity": 1.0}  # type: ignore[method-assign]
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
async def test_voice_session_interrupts_active_playback_and_cleans_audio(tmp_path):
    manager = VoiceSessionManager(
        VoiceConfig(
            enabled=True,
            fingerprint_path=tmp_path / "fingerprint.json",
            retain_raw_audio=False,
        ),
        companion_callback=companion,
    )
    playback_started = asyncio.Event()
    manager.recorder.record = fake_record  # type: ignore[method-assign]
    manager.fingerprint.verify = lambda path: {  # type: ignore[method-assign]
        "owner_voice_matched": True,
        "similarity": 1.0,
    }
    manager.stt.transcribe = fake_transcribe  # type: ignore[method-assign]
    manager.tts.synthesize = fake_synthesize  # type: ignore[method-assign]

    async def wait_for_interrupt(path, *, stop_event):
        assert Path(path).is_file()
        playback_started.set()
        await stop_event.wait()

    manager.player.play = wait_for_interrupt  # type: ignore[method-assign]
    manager._temporary_audio_path = (  # type: ignore[method-assign]
        lambda prefix, suffix=".wav": tmp_path / f"{prefix}{suffix}"
    )

    task = asyncio.create_task(manager.run_once(session_id="voice-test"))
    await asyncio.wait_for(playback_started.wait(), timeout=2)
    status = manager.interrupt()
    result = await asyncio.wait_for(task, timeout=2)

    assert status["interrupted"] is True
    assert status["last_status"] == "interrupted"
    assert result["status"] == "interrupted"
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
    manager.fingerprint.verify = lambda path: {"owner_voice_matched": True, "similarity": 1.0}  # type: ignore[method-assign]

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

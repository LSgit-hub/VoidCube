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
from systems.voice.session import VoiceSessionManager, _extract_wake_query
from systems.voice.stt import SpeechToText
from systems.voice.vad import SpeechEndpointDetector
from systems.voice.wake import (
    KWS_DECODER,
    KWS_ENCODER,
    KWS_JOINER,
    KWS_MODEL_NAME,
    WakeWordDetector,
)


@pytest.mark.unit
def test_voice_config_uses_hello_stellar_as_default_wake_phrase(monkeypatch):
    monkeypatch.delenv("VOIDCUBE_VOICE_WAKE_WORD", raising=False)
    monkeypatch.delenv("VOIDCUBE_STT_HOTWORDS", raising=False)

    config = VoiceConfig.from_env()

    assert config.wake_word == "你好，星子"
    assert config.wake_keyword_tokens == "n ǐ h ǎo x īng z ǐ @你好星子"
    assert config.wake_cue_enabled is True
    assert config.speech_start_timeout_seconds == 8.0
    assert config.speech_end_silence_seconds == 3.0
    assert config.max_utterance_seconds == 45.0
    assert config.stt_hotwords == "你好 星子 西子 VoidCube 语音系统"


@pytest.mark.unit
@pytest.mark.parametrize(
    ("transcript", "expected_query"),
    [
        ("你好，星子", ""),
        ("你好星子。请告诉我当前任务", "请告诉我当前任务"),
        ("你好, 星子！ 当前任务是什么？", "当前任务是什么？"),
        ("我只是提到了星子", None),
    ],
)
def test_wake_phrase_tolerates_stt_punctuation_without_short_word_fallback(
    transcript,
    expected_query,
):
    assert _extract_wake_query(transcript, "你好，星子") == expected_query


@pytest.mark.unit
def test_wake_word_detector_uses_local_streaming_keyword_runtime(tmp_path, monkeypatch):
    model_dir = tmp_path / KWS_MODEL_NAME
    model_dir.mkdir()
    for name in ("tokens.txt", KWS_ENCODER, KWS_DECODER, KWS_JOINER):
        (model_dir / name).write_bytes(b"model")
    runtime: dict[str, object] = {}

    class FakeStream:
        ready = False

        def accept_waveform(self, sample_rate, samples):
            runtime["sample_rate"] = sample_rate
            runtime["samples"] = samples
            self.ready = True

    class FakeSpotter:
        def __init__(self, **kwargs):
            runtime["config"] = kwargs

        def create_stream(self):
            return FakeStream()

        def is_ready(self, stream):
            return stream.ready

        def decode_stream(self, stream):
            stream.ready = False

        def get_result(self, stream):
            del stream
            return "你好星子"

        def reset_stream(self, stream):
            runtime["reset"] = stream

    monkeypatch.setitem(
        sys.modules,
        "sherpa_onnx",
        SimpleNamespace(KeywordSpotter=FakeSpotter),
    )
    detector = WakeWordDetector(
        tmp_path,
        keyword_tokens="n ǐ h ǎo x īng z ǐ @你好星子",
    )

    detector.reset()
    result = detector.accept([0.1] * 512)

    assert result == "你好星子"
    assert runtime["sample_rate"] == 16000
    assert runtime["config"]["provider"] == "cpu"  # type: ignore[index]
    assert runtime["config"]["keywords_threshold"] == 0.25  # type: ignore[index]
    assert (model_dir / "voidcube-keywords.txt").read_text(encoding="utf-8") == (
        "n ǐ h ǎo x īng z ǐ @你好星子\n"
    )


@pytest.mark.unit
def test_silero_vad_uses_three_second_endpoint_and_returns_complete_segment(
    tmp_path,
    monkeypatch,
):
    model_path = tmp_path / "silero_vad.onnx"
    model_path.write_bytes(b"model")
    runtime: dict[str, object] = {}

    class FakeSileroConfig:
        def __init__(self, **kwargs):
            runtime["silero"] = kwargs

    class FakeVadConfig:
        def __init__(self, **kwargs):
            runtime["vad"] = kwargs

        def validate(self):
            return True

    class FakeVad:
        def __init__(self, config, *, buffer_size_in_seconds):
            runtime["buffer"] = buffer_size_in_seconds
            runtime["config"] = config
            self.has_segment = False
            self.front = SimpleNamespace(samples=[0.1, 0.2, 0.3])

        def reset(self):
            self.has_segment = False

        def accept_waveform(self, samples):
            runtime["samples"] = samples
            self.has_segment = True

        def is_speech_detected(self):
            return self.has_segment

        def empty(self):
            return not self.has_segment

        def pop(self):
            self.has_segment = False

    monkeypatch.setitem(
        sys.modules,
        "sherpa_onnx",
        SimpleNamespace(
            SileroVadModelConfig=FakeSileroConfig,
            VadModelConfig=FakeVadConfig,
            VoiceActivityDetector=FakeVad,
        ),
    )
    detector = SpeechEndpointDetector(model_path, min_silence_seconds=3.0)
    detector.ensure_model = lambda: model_path  # type: ignore[method-assign]

    detector.reset()
    detector.accept([0.1] * 512)
    utterance = detector.pop_utterance()

    assert runtime["silero"]["min_silence_duration"] == 3.0  # type: ignore[index]
    assert runtime["silero"]["window_size"] == 512  # type: ignore[index]
    assert runtime["buffer"] == 55.0
    assert utterance == [0.1, 0.2, 0.3]
    assert detector.pop_utterance() is None


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


@pytest.mark.asyncio
@pytest.mark.unit
async def test_local_stt_returns_empty_text_for_silent_audio(tmp_path, monkeypatch):
    class SilentModel:
        def __init__(self, model, *, device, compute_type):
            pass

        def transcribe(self, path, **kwargs):
            return iter(()), SimpleNamespace()

    monkeypatch.setitem(
        sys.modules,
        "faster_whisper",
        SimpleNamespace(WhisperModel=SilentModel),
    )
    stt = SpeechToText(
        provider="local",
        base_url="",
        api_key="",
        model="base",
    )
    audio_path = tmp_path / "silence.wav"
    audio_path.write_bytes(b"audio")

    assert await stt.transcribe(audio_path) == ""


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


def _install_single_utterance_stream(
    manager: VoiceSessionManager,
) -> list[str]:
    observations: list[str] = []

    class FakeInputStream:
        active = False

        def start(self, loop):
            del loop
            self.active = True
            observations.append("input_started")

        async def read(self):
            assert manager.state.active is True
            assert manager.state.wake_state == "listening"
            assert manager.state.meter_active is True
            observations.append("meter_active")
            return SimpleNamespace(
                samples=[0.2] * 512,
                level=0.72,
                peak=0.81,
                rms=0.2,
            )

        def flush(self):
            pass

        def stop(self):
            if self.active:
                observations.append("input_stopped")
            self.active = False

    class FakeEndpointDetector:
        model_ready = True

        def __init__(self):
            self.has_utterance = False

        def ensure_model(self):
            observations.append("vad_ready")

        def reset(self):
            self.has_utterance = False

        @property
        def speech_active(self):
            return self.has_utterance

        def accept(self, samples):
            del samples
            self.has_utterance = True

        def pop_utterance(self):
            if not self.has_utterance:
                return None
            self.has_utterance = False
            return [0.2] * 3200

    manager.input_stream = FakeInputStream()  # type: ignore[assignment]
    manager.endpoint_detector = FakeEndpointDetector()  # type: ignore[assignment]
    return observations


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
    _install_single_utterance_stream(manager)
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
    _install_single_utterance_stream(manager)
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
    observations = _install_single_utterance_stream(manager)
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
    assert manager.status()["wake_state"] == "idle"
    assert manager.status()["meter_active"] is False
    assert manager.status()["utterance_seconds"] == 0.2
    assert observations == [
        "vad_ready",
        "input_started",
        "meter_active",
        "input_stopped",
    ]
    assert not (tmp_path / "utterance.wav").exists()
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
    _install_single_utterance_stream(manager)
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
    assert not (tmp_path / "utterance.wav").exists()
    assert not (tmp_path / "reply.mp3").exists()


@pytest.mark.asyncio
@pytest.mark.unit
async def test_voice_session_button_can_interrupt_while_waiting_for_speech():
    manager = VoiceSessionManager(VoiceConfig(enabled=True))
    listening = asyncio.Event()

    class WaitingInputStream:
        active = False

        def start(self, loop):
            del loop
            self.active = True

        async def read(self):
            listening.set()
            await asyncio.Event().wait()

        def flush(self):
            pass

        def stop(self):
            self.active = False

    class SilentEndpointDetector:
        model_ready = True
        speech_active = False

        def ensure_model(self):
            pass

        def reset(self):
            pass

        def accept(self, samples):
            del samples

        def pop_utterance(self):
            return None

    manager.input_stream = WaitingInputStream()  # type: ignore[assignment]
    manager.endpoint_detector = SilentEndpointDetector()  # type: ignore[assignment]

    task = asyncio.create_task(manager.run_once(session_id="button-interrupt"))
    await asyncio.wait_for(listening.wait(), timeout=1)
    interrupt_status = manager.interrupt()
    result = await asyncio.wait_for(task, timeout=1)

    assert interrupt_status["interrupted"] is True
    assert result == {"status": "interrupted", "reason": "recording_interrupted"}
    assert manager.state.active is False
    assert manager.state.wake_state == "idle"
    assert manager.state.meter_active is False


@pytest.mark.asyncio
@pytest.mark.unit
async def test_continuous_voice_uses_kws_vad_and_returns_to_wake_standby(tmp_path):
    config = VoiceConfig(
        enabled=True,
        fingerprint_path=tmp_path / "fingerprint.json",
        retain_raw_audio=False,
        wake_word="你好，星子",
        wake_word_required=True,
        fingerprint_enabled=True,
    )
    callbacks: list[str] = []
    synthesized: list[str] = []
    transitions: list[str] = []

    async def companion_for_continuous(*, text: str, session_id: str):
        callbacks.append(text)
        transitions.append(f"companion:{manager.state.last_status}")
        return {"status": "ok", "reply_text": "当前正在处理记忆隔离。"}

    manager = VoiceSessionManager(config, companion_callback=companion_for_continuous)

    class FakeInputStream:
        def __init__(self):
            self.frames = iter(
                [
                    SimpleNamespace(samples=[0.0] * 512, level=0.1, peak=0.1, rms=0.01),
                    SimpleNamespace(samples=[0.2] * 512, level=0.7, peak=0.8, rms=0.2),
                    SimpleNamespace(samples=[0.0] * 512, level=0.2, peak=0.2, rms=0.02),
                ]
            )
            self.start_count = 0
            self.active = False

        def start(self, loop):
            del loop
            self.start_count += 1
            self.active = True
            if self.start_count == 2:
                manager._continuous_stop_event.set()

        async def read(self):
            await asyncio.sleep(0)
            return next(self.frames)

        def flush(self):
            transitions.append("input:flushed")

        def stop(self):
            self.active = False

    class FakeWakeDetector:
        model_ready = True

        def ensure_model(self):
            transitions.append("wake:model_ready")

        def reset(self):
            transitions.append("wake:reset")

        def accept(self, samples):
            del samples
            transitions.append(f"wake:{manager.state.wake_state}")
            return "你好星子"

    class FakeEndpointDetector:
        model_ready = True

        def __init__(self):
            self.accept_count = 0

        def ensure_model(self):
            transitions.append("vad:model_ready")

        def reset(self):
            self.accept_count = 0
            transitions.append("vad:reset")

        @property
        def speech_active(self):
            return self.accept_count > 0

        def accept(self, samples):
            del samples
            self.accept_count += 1
            transitions.append(f"vad:{manager.state.wake_state}")

        def pop_utterance(self):
            if self.accept_count < 2:
                return None
            return [0.2] * 3200

    manager.input_stream = FakeInputStream()  # type: ignore[assignment]
    manager.wake_detector = FakeWakeDetector()  # type: ignore[assignment]
    manager.endpoint_detector = FakeEndpointDetector()  # type: ignore[assignment]

    def verify_complete_utterance(path):
        assert Path(path).is_file()
        assert manager.input_stream.active is False
        transitions.append(f"fingerprint:{manager.state.last_status}")
        return {"owner_voice_matched": True, "similarity": 0.91, "reason": "matched"}

    manager.fingerprint.verify = verify_complete_utterance  # type: ignore[method-assign]

    async def transcribe_once(path):
        assert Path(path).is_file()
        assert manager.input_stream.active is False
        transitions.append(f"stt:{manager.state.last_status}")
        return "请告诉我当前任务"

    manager.stt.transcribe = transcribe_once  # type: ignore[method-assign]

    async def synthesize_sequence(text, path):
        assert manager.input_stream.active is False
        synthesized.append(text)
        transitions.append(f"tts:{manager.state.last_status}")
        return await fake_synthesize(text, path)

    manager.tts.synthesize = synthesize_sequence  # type: ignore[method-assign]

    async def play_reply(path, *, stop_event):
        assert Path(path).is_file()
        assert not stop_event.is_set()
        transitions.append(f"play:{manager.state.last_status}")

    async def play_wake_cue():
        transitions.append(f"cue:{manager.state.wake_state}")

    manager.player.play = play_reply  # type: ignore[method-assign]
    manager.player.play_wake_cue = play_wake_cue  # type: ignore[method-assign]
    manager._temporary_audio_path = (  # type: ignore[method-assign]
        lambda prefix, suffix=".wav": tmp_path / f"{prefix}-{len(callbacks)}{suffix}"
    )
    manager.state.continuous_active = True
    manager.state.session_id = "continuous-test"

    await asyncio.wait_for(manager._run_streaming_voice_loop(), timeout=2)

    assert callbacks == ["请告诉我当前任务"]
    assert manager.state.wake_word_hits == 1
    assert manager.state.last_transcript == "请告诉我当前任务"
    assert manager.state.last_reply == "当前正在处理记忆隔离。"
    assert manager.state.wake_state == "standby"
    assert manager.state.last_status == "awaiting_wake_word"
    assert manager.state.meter_active is False
    assert synthesized == ["当前正在处理记忆隔离。"]
    assert transitions.count("wake:standby") == 1
    assert transitions.count("vad:listening") == 2
    assert transitions.count("fingerprint:matching_owner_voice") == 1
    assert "cue:wake_detected" in transitions
    assert "stt:transcribing" in transitions
    assert "companion:thinking" in transitions
    assert "tts:speaking" in transitions
    assert "play:speaking" in transitions
    assert not list(tmp_path.glob("*.wav"))
    assert not list(tmp_path.glob("*.mp3"))


@pytest.mark.asyncio
@pytest.mark.unit
async def test_continuous_voice_retries_transient_stream_failure_without_disabling():
    manager = VoiceSessionManager(VoiceConfig(enabled=True))
    attempts = 0
    retry_state: dict[str, object] = {}

    async def flaky_stream_loop():
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("temporary microphone stream failure")
        retry_state.update(
            continuous_active=manager.state.continuous_active,
            last_status=manager.state.last_status,
            wake_state=manager.state.wake_state,
        )
        manager._continuous_stop_event.set()

    manager._run_streaming_voice_loop = flaky_stream_loop  # type: ignore[method-assign]

    await asyncio.wait_for(manager._continuous_loop(), timeout=2)

    assert attempts == 2
    assert retry_state == {
        "continuous_active": True,
        "last_status": "continuous_retrying",
        "wake_state": "starting",
    }
    assert manager.state.continuous_error_count == 1
    assert manager.state.last_error == (
        "RuntimeError: temporary microphone stream failure"
    )


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

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import logging
from pathlib import Path
import tempfile
import time
from typing import Any, Awaitable, Callable
import unicodedata
import uuid

from systems.voice.audio import AudioInputStream, AudioPlayer, AudioRecorder
from systems.voice.config import VoiceConfig
from systems.voice.fingerprint import FingerprintStore
from systems.voice.stt import SpeechToText
from systems.voice.tts import TextToSpeech
from systems.voice.vad import SpeechEndpointDetector
from systems.voice.wake import WakeWordDetector


CompanionCallback = Callable[..., Awaitable[dict[str, Any]]]
_WAKE_QUERY_TRIM = " \t\r\n，,。.!！？?：:、；;"
logger = logging.getLogger(__name__)


def _compact_wake_text(value: str) -> tuple[str, list[int]]:
    compact: list[str] = []
    source_ends: list[int] = []
    for index, character in enumerate(str(value or "")):
        normalized = unicodedata.normalize("NFKC", character).casefold()
        for token in normalized:
            category = unicodedata.category(token)
            if token.isspace() or category.startswith(("P", "Z")):
                continue
            compact.append(token)
            source_ends.append(index + 1)
    return "".join(compact), source_ends


def _extract_wake_query(transcript: str, wake_word: str) -> str | None:
    """Return text after a wake phrase, tolerating STT punctuation differences."""
    compact_transcript, source_ends = _compact_wake_text(transcript)
    compact_wake_word, _ = _compact_wake_text(wake_word)
    if not compact_wake_word:
        return None
    wake_index = compact_transcript.find(compact_wake_word)
    if wake_index < 0:
        return None
    source_end = source_ends[wake_index + len(compact_wake_word) - 1]
    return transcript[source_end:].lstrip(_WAKE_QUERY_TRIM)


@dataclass(slots=True)
class VoiceRuntimeState:
    enabled: bool = False
    active: bool = False
    continuous_active: bool = False
    wake_state: str = "idle"
    wake_word_hits: int = 0
    continuous_error_count: int = 0
    last_listen_transcript: str = ""
    audio_level: float = 0.0
    audio_peak: float = 0.0
    audio_rms: float = 0.0
    meter_active: bool = False
    speech_detected: bool = False
    utterance_seconds: float = 0.0
    session_id: str = ""
    last_status: str = "idle"
    last_error: str = ""
    last_transcript: str = ""
    last_reply: str = ""
    owner_voice_matched: bool = False
    fingerprint_enabled: bool = True
    last_fingerprint_reason: str = "not_checked"
    last_fingerprint_similarity: float | None = None
    enrollment_sample_index: int = 0
    enrollment_sample_count: int = 0
    interrupted: bool = False


class VoiceSessionManager:
    """Single-owner voice transport with speaker filtering and no user accounts."""

    def __init__(
        self,
        config: VoiceConfig | None = None,
        *,
        companion_callback: CompanionCallback | None = None,
    ) -> None:
        self.config = config or VoiceConfig.from_env()
        self.companion_callback = companion_callback
        self.state = VoiceRuntimeState(
            enabled=self.config.enabled,
            fingerprint_enabled=self.config.fingerprint_enabled,
        )
        self.recorder = AudioRecorder(
            sample_rate=self.config.sample_rate,
            channels=self.config.channels,
        )
        self.input_stream = AudioInputStream(
            sample_rate=self.config.sample_rate,
            channels=self.config.channels,
            frame_samples=512,
        )
        self.player = AudioPlayer()
        self.wake_detector = WakeWordDetector(
            self.config.wake_model_root,
            keyword_tokens=self.config.wake_keyword_tokens,
            sample_rate=self.config.sample_rate,
            threshold=self.config.wake_threshold,
            score=self.config.wake_score,
        )
        self.endpoint_detector = SpeechEndpointDetector(
            self.config.vad_model_path,
            sample_rate=self.config.sample_rate,
            threshold=self.config.vad_threshold,
            min_silence_seconds=self.config.speech_end_silence_seconds,
            max_speech_seconds=self.config.max_utterance_seconds,
        )
        self.fingerprint = FingerprintStore(
            self.config.fingerprint_path,
            threshold=self.config.fingerprint_threshold,
            model_path=self.config.fingerprint_model_path,
        )
        self.stt = SpeechToText(
            provider=self.config.stt_provider,
            base_url=self.config.stt_base_url,
            api_key=self.config.stt_api_key,
            model=self.config.stt_model,
            language=self.config.stt_language,
            hotwords=self.config.stt_hotwords,
            device=self.config.stt_device,
            compute_type=self.config.stt_compute_type,
        )
        self.tts = TextToSpeech(
            provider=self.config.tts_provider,
            voice=self.config.tts_voice,
            base_url=self.config.tts_base_url,
            api_key=self.config.tts_api_key,
            model=self.config.tts_model,
        )
        self._stop_event = asyncio.Event()
        self._continuous_stop_event = asyncio.Event()
        self._continuous_task: asyncio.Task[Any] | None = None
        self._lock = asyncio.Lock()

    def status(self) -> dict[str, Any]:
        task = self._continuous_task
        return {
            "enabled": self.state.enabled,
            "active": self.state.active,
            "continuous_active": self.state.continuous_active,
            "continuous_task_running": task is not None and not task.done(),
            "wake_state": self.state.wake_state,
            "wake_word": self.config.wake_word,
            "wake_word_required": self.config.wake_word_required,
            "wake_word_hits": self.state.wake_word_hits,
            "wake_engine": "sherpa-onnx-keyword-spotter",
            "wake_model_ready": self.wake_detector.model_ready,
            "vad_engine": "sherpa-onnx-silero-vad",
            "vad_model_ready": self.endpoint_detector.model_ready,
            "speech_end_silence_seconds": self.config.speech_end_silence_seconds,
            "continuous_error_count": self.state.continuous_error_count,
            "last_listen_transcript": self.state.last_listen_transcript,
            "audio_level": round(self.state.audio_level, 4),
            "audio_peak": round(self.state.audio_peak, 4),
            "audio_rms": round(self.state.audio_rms, 6),
            "meter_active": self.state.meter_active,
            "speech_detected": self.state.speech_detected,
            "utterance_seconds": round(self.state.utterance_seconds, 3),
            "input_stream_active": self.input_stream.active,
            "session_id": self.state.session_id,
            "last_status": self.state.last_status,
            "last_error": self.state.last_error,
            "last_transcript": self.state.last_transcript,
            "last_reply": self.state.last_reply,
            "owner_voice_matched": self.state.owner_voice_matched,
            "fingerprint_enabled": self.state.fingerprint_enabled,
            "fingerprint_status": self._fingerprint_status(),
            "last_fingerprint_reason": self.state.last_fingerprint_reason,
            "last_fingerprint_similarity": self.state.last_fingerprint_similarity,
            "fingerprint_threshold": self.fingerprint.threshold,
            "fingerprint_engine": "sherpa-onnx-campplus",
            "fingerprint_model_ready": self.fingerprint.model_ready,
            "fingerprint_template_status": self.fingerprint.template_status,
            "enrollment_sample_index": self.state.enrollment_sample_index,
            "enrollment_sample_count": self.state.enrollment_sample_count,
            "interrupted": self.state.interrupted,
            "owner_voice_template_present": (
                self.fingerprint.template_status == "enrolled"
            ),
            "stt_provider": self.stt.provider,
            "stt_model": self.stt.model,
            "stt_configured": self.stt.configured,
            "tts_configured": self.tts.configured,
            "capture_available": self.recorder.available(),
            "playback_available": self.player.available(),
            "raw_audio_retained": self.config.retain_raw_audio,
        }

    def realtime_status(self) -> dict[str, Any]:
        return {
            "continuous_active": self.state.continuous_active,
            "continuous_task_running": (
                self._continuous_task is not None
                and not self._continuous_task.done()
            ),
            "active": self.state.active,
            "wake_state": self.state.wake_state,
            "last_status": self.state.last_status,
            "last_error": self.state.last_error,
            "audio_level": round(self.state.audio_level, 4),
            "audio_peak": round(self.state.audio_peak, 4),
            "audio_rms": round(self.state.audio_rms, 6),
            "meter_active": self.state.meter_active,
            "speech_detected": self.state.speech_detected,
            "utterance_seconds": round(self.state.utterance_seconds, 3),
        }

    def set_enabled(self, enabled: bool) -> dict[str, Any]:
        self.state.enabled = bool(enabled)
        if not enabled:
            self.interrupt()
        return self.status()

    def set_fingerprint_enabled(self, enabled: bool) -> dict[str, Any]:
        self.state.fingerprint_enabled = bool(enabled)
        self.state.owner_voice_matched = False
        template_status = self.fingerprint.template_status
        self.state.last_fingerprint_reason = "fingerprint_disabled"
        if enabled:
            self.state.last_fingerprint_reason = (
                "owner_voice_template_upgrade_required"
                if template_status == "upgrade_required"
                else "not_checked"
            )
        self.state.last_fingerprint_similarity = None
        return self.status()

    def interrupt(self) -> dict[str, Any]:
        task = self._continuous_task
        was_active = bool(
            self.state.active
            or self.state.continuous_active
            or (task is not None and not task.done())
        )
        self._stop_event.set()
        self._continuous_stop_event.set()
        self.input_stream.stop()
        self.player.stop()
        if task is not None and not task.done():
            task.cancel()
        self.state.interrupted = was_active
        if was_active:
            self.state.last_status = "interrupted"
        return self.status()

    async def record_owner_template(
        self,
        *,
        duration_seconds: float = 3.0,
        sample_count: int = 3,
    ) -> dict[str, Any]:
        if not self.state.enabled:
            return {"status": "disabled", "reason": "voice_disabled"}
        if self.state.continuous_active:
            return {"status": "busy", "reason": "continuous_listening_active"}
        async with self._lock:
            self.state.active = True
            self.state.last_error = ""
            self.state.last_status = "recording_owner_voice_template"
            self.state.enrollment_sample_index = 0
            self.state.enrollment_sample_count = max(2, min(5, int(sample_count)))
            self._stop_event = asyncio.Event()
            paths: list[Path] = []
            try:
                for index in range(self.state.enrollment_sample_count):
                    self.state.enrollment_sample_index = index + 1
                    path = self._temporary_audio_path(
                        f"owner-template-{index + 1}"
                    )
                    paths.append(path)
                    await self.recorder.record(
                        path,
                        duration_seconds=min(
                            duration_seconds,
                            self.config.max_record_seconds,
                        ),
                        stop_event=self._stop_event,
                    )
                    if self._stop_event.is_set():
                        self.state.last_status = "interrupted"
                        return {
                            "status": "interrupted",
                            "reason": "recording_interrupted",
                        }
                self.state.last_status = "building_owner_voice_template"
                result = await asyncio.to_thread(
                    self.fingerprint.record_owner_templates,
                    paths,
                )
                self.state.last_status = "owner_voice_template_recorded"
                self.state.last_fingerprint_reason = "template_recorded"
                self.state.last_fingerprint_similarity = None
                return {**result, "status": "owner_voice_template_recorded"}
            except Exception as exc:
                self.state.last_status = "error"
                self.state.last_error = f"{type(exc).__name__}: {exc}"
                return {"status": "error", "reason": self.state.last_error}
            finally:
                self.state.active = False
                for path in paths:
                    self._cleanup(path)

    async def run_once(
        self,
        *,
        session_id: str = "",
    ) -> dict[str, Any]:
        if not self.state.enabled:
            return {"status": "disabled", "reason": "voice_disabled"}
        if self.state.continuous_active:
            return {"status": "busy", "reason": "continuous_listening_active"}
        async with self._lock:
            self._prepare_session(session_id)
            try:
                await asyncio.to_thread(self.endpoint_detector.ensure_model)
                self.endpoint_detector.reset()
                self.input_stream.start(asyncio.get_running_loop())
                speech_deadline = self._begin_utterance_capture()

                while self.state.enabled and not self._stop_event.is_set():
                    try:
                        frame = await asyncio.wait_for(
                            self.input_stream.read(),
                            timeout=0.25,
                        )
                    except asyncio.TimeoutError:
                        continue
                    utterance = self._accept_utterance_frame(frame)
                    if utterance:
                        self.input_stream.stop()
                        return await self._process_captured_utterance(
                            utterance,
                            stop_event=self._stop_event,
                            strip_wake_word=False,
                        )

                    if (
                        not self.state.speech_detected
                        and time.monotonic() >= speech_deadline
                    ):
                        self.state.last_status = "speech_start_timeout"
                        return {
                            "status": "empty",
                            "reason": "speech_start_timeout",
                        }

                self.state.last_status = "interrupted"
                return {"status": "interrupted", "reason": "recording_interrupted"}
            except asyncio.CancelledError:
                self.interrupt()
                raise
            except Exception as exc:
                return self._record_error(exc)
            finally:
                self.input_stream.stop()
                self.state.active = False
                self.state.wake_state = "idle"
                self.state.meter_active = False
                self.state.speech_detected = False
                self.state.audio_level = 0.0
                self.state.audio_peak = 0.0
                self.state.audio_rms = 0.0

    async def transcribe_once(
        self,
        *,
        session_id: str = "",
    ) -> dict[str, Any]:
        """Capture one utterance and return its transcript without side effects."""
        if not self.state.enabled:
            return {"status": "disabled", "reason": "voice_disabled"}
        if self.state.continuous_active:
            return {"status": "busy", "reason": "continuous_listening_active"}
        async with self._lock:
            self._prepare_session(session_id)
            try:
                await asyncio.to_thread(self.endpoint_detector.ensure_model)
                self.endpoint_detector.reset()
                self.input_stream.start(asyncio.get_running_loop())
                speech_deadline = self._begin_utterance_capture()

                while self.state.enabled and not self._stop_event.is_set():
                    try:
                        frame = await asyncio.wait_for(
                            self.input_stream.read(),
                            timeout=0.25,
                        )
                    except asyncio.TimeoutError:
                        continue
                    utterance = self._accept_utterance_frame(frame)
                    if utterance:
                        self.input_stream.stop()
                        return await self._transcribe_captured_utterance(
                            utterance,
                            stop_event=self._stop_event,
                        )

                    if (
                        not self.state.speech_detected
                        and time.monotonic() >= speech_deadline
                    ):
                        self.state.last_status = "speech_start_timeout"
                        return {
                            "status": "empty",
                            "reason": "speech_start_timeout",
                        }

                self.state.last_status = "interrupted"
                return {"status": "interrupted", "reason": "recording_interrupted"}
            except asyncio.CancelledError:
                self.interrupt()
                raise
            except Exception as exc:
                return self._record_error(exc)
            finally:
                self.input_stream.stop()
                self.state.active = False
                self.state.wake_state = "idle"
                self.state.meter_active = False
                self.state.speech_detected = False
                self.state.audio_level = 0.0
                self.state.audio_peak = 0.0
                self.state.audio_rms = 0.0

    async def speak_text(self, text: str, *, reason: str = "proactive") -> dict[str, Any]:
        """Play already-authorized text without opening the microphone."""
        message = str(text or "").strip()
        if not message:
            return {"status": "invalid", "reason": "text_is_empty"}
        if not self.state.enabled:
            return {"status": "disabled", "reason": "voice_disabled"}
        if self.state.active or self.state.continuous_active:
            return {"status": "busy", "reason": "voice_session_active"}

        async with self._lock:
            self.state.active = True
            self.state.last_status = "speaking"
            self.state.last_error = ""
            self.state.interrupted = False
            self.state.last_reply = message[:4000]
            self._stop_event = asyncio.Event()
            speech_path = self._temporary_audio_path("proactive", suffix=".mp3")
            try:
                await self.tts.synthesize(message, speech_path)
                await self.player.play(speech_path, stop_event=self._stop_event)
                if self._stop_event.is_set():
                    self.state.last_status = "interrupted"
                    return {
                        "status": "interrupted",
                        "reply_text": message,
                        "reason": reason,
                    }
                self.state.last_status = "complete"
                return {
                    "status": "complete",
                    "reply_text": message,
                    "reason": reason,
                }
            except asyncio.CancelledError:
                self.interrupt()
                raise
            except Exception as exc:
                return self._record_error(exc)
            finally:
                self.state.active = False
                self._cleanup(speech_path)

    def start_continuous(self, *, session_id: str = "") -> dict[str, Any]:
        if not self.state.enabled:
            return {"status": "disabled", "reason": "voice_disabled"}
        task = self._continuous_task
        if task is not None and not task.done():
            return {"status": "already_running", **self.status()}
        if self.state.active:
            return {"status": "busy", "reason": "voice_session_active"}
        self._continuous_stop_event = asyncio.Event()
        self.state.session_id = str(session_id or "").strip() or f"voice-continuous-{uuid.uuid4()}"
        self.state.interrupted = False
        self.state.last_error = ""
        self.state.last_listen_transcript = ""
        self.state.continuous_error_count = 0
        self.state.audio_level = 0.0
        self.state.audio_peak = 0.0
        self.state.audio_rms = 0.0
        self.state.meter_active = False
        self.state.speech_detected = False
        self.state.utterance_seconds = 0.0
        self.state.wake_state = "starting"
        self.state.last_status = "preparing_voice_models"
        self._continuous_task = asyncio.create_task(self._continuous_loop())
        return {"status": "started", **self.status()}

    async def stop_continuous(self) -> dict[str, Any]:
        task = self._continuous_task
        was_running = bool(
            self.state.continuous_active
            or (task is not None and not task.done())
        )
        self._continuous_stop_event.set()
        self._stop_event.set()
        self.player.stop()
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        self._continuous_task = None
        self.state.continuous_active = False
        self.state.active = False
        self.state.wake_state = "idle"
        self.state.meter_active = False
        self.state.audio_level = 0.0
        self.state.audio_peak = 0.0
        self.state.audio_rms = 0.0
        if was_running:
            self.state.last_status = "continuous_stopped"
        return {"status": "stopped" if was_running else "already_stopped", **self.status()}

    async def _continuous_loop(self) -> None:
        consecutive_errors = 0
        self.state.continuous_active = True
        try:
            while self.state.enabled and not self._continuous_stop_event.is_set():
                try:
                    await self._run_streaming_voice_loop()
                    break
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    consecutive_errors += 1
                    self.state.continuous_error_count += 1
                    self.state.last_error = f"{type(exc).__name__}: {exc}"
                    self.state.last_status = "continuous_retrying"
                    self.state.wake_state = "starting"
                    self.state.active = False
                    self.state.meter_active = False
                    self.input_stream.stop()
                    if consecutive_errors == 1 or consecutive_errors % 10 == 0:
                        logger.warning(
                            "Streaming voice loop failed; listening will retry",
                            exc_info=True,
                        )
                    await asyncio.sleep(min(2.0, 0.25 * consecutive_errors))
        except asyncio.CancelledError:
            raise
        finally:
            self.input_stream.stop()
            self.player.stop()
            self.state.active = False
            self.state.continuous_active = False
            self.state.wake_state = "idle"
            self.state.meter_active = False
            self.state.audio_level = 0.0
            self.state.audio_peak = 0.0
            self.state.audio_rms = 0.0
            self._continuous_task = None

    async def _run_streaming_voice_loop(self) -> None:
        self.state.last_status = "preparing_voice_models"
        model_tasks = [asyncio.to_thread(self.endpoint_detector.ensure_model)]
        if self.config.wake_word_required:
            model_tasks.append(asyncio.to_thread(self.wake_detector.ensure_model))
        await asyncio.gather(*model_tasks)

        loop = asyncio.get_running_loop()
        self.endpoint_detector.reset()
        if self.config.wake_word_required:
            self.wake_detector.reset()
        self.input_stream.start(loop)
        self._return_to_wake_standby()
        speech_deadline = 0.0

        while self.state.enabled and not self._continuous_stop_event.is_set():
            frame = await self.input_stream.read()

            if self.state.wake_state == "standby":
                if not self.config.wake_word_required:
                    speech_deadline = self._begin_utterance_capture()
                    continue
                keyword = self.wake_detector.accept(frame.samples)
                if keyword:
                    self.state.wake_word_hits += 1
                    self.state.last_status = "wake_word_detected"
                    self.state.wake_state = "wake_detected"
                    self.state.last_error = ""
                    self.state.audio_level = 0.0
                    self.state.audio_peak = 0.0
                    self.state.audio_rms = 0.0
                    if self.config.wake_cue_enabled:
                        await self.player.play_wake_cue()
                    self.input_stream.flush()
                    speech_deadline = self._begin_utterance_capture()
                continue

            if self.state.wake_state != "listening":
                continue

            utterance = self._accept_utterance_frame(frame)
            if utterance:
                self.state.active = True
                self.input_stream.stop()
                await self._process_captured_utterance(
                    utterance,
                    stop_event=self._continuous_stop_event,
                    strip_wake_word=True,
                )
                self.state.active = False
                if self._continuous_stop_event.is_set() or not self.state.enabled:
                    return
                self.endpoint_detector.reset()
                if self.config.wake_word_required:
                    self.wake_detector.reset()
                self.input_stream.start(loop)
                self._return_to_wake_standby()
                continue

            if not self.state.speech_detected and time.monotonic() >= speech_deadline:
                self.endpoint_detector.reset()
                self._return_to_wake_standby()

    def _begin_utterance_capture(self) -> float:
        self.endpoint_detector.reset()
        self.state.wake_state = "listening"
        self.state.last_status = "listening_for_speech"
        self.state.meter_active = True
        self.state.speech_detected = False
        self.state.utterance_seconds = 0.0
        return time.monotonic() + self.config.speech_start_timeout_seconds

    def _return_to_wake_standby(self) -> None:
        self.state.wake_state = "standby"
        self.state.last_status = "awaiting_wake_word"
        self.state.meter_active = False
        self.state.speech_detected = False
        self.state.audio_level = 0.0
        self.state.audio_peak = 0.0
        self.state.audio_rms = 0.0

    def _update_audio_level(self, frame: Any) -> None:
        if not self.state.meter_active:
            return
        self.state.audio_level = float(frame.level)
        self.state.audio_peak = float(frame.peak)
        self.state.audio_rms = float(frame.rms)

    def _accept_utterance_frame(self, frame: Any) -> list[float] | None:
        self._update_audio_level(frame)
        self.endpoint_detector.accept(frame.samples)
        self.state.speech_detected = (
            self.state.speech_detected or self.endpoint_detector.speech_active
        )
        utterance = self.endpoint_detector.pop_utterance()
        if not utterance:
            return None
        self.state.utterance_seconds = len(utterance) / float(self.config.sample_rate)
        self.state.meter_active = False
        self.state.wake_state = "finalizing"
        self.state.last_status = "finalizing_utterance"
        return utterance

    async def _process_captured_utterance(
        self,
        samples: list[float],
        *,
        stop_event: asyncio.Event,
        strip_wake_word: bool,
    ) -> dict[str, Any]:
        audio_path = self._temporary_audio_path("utterance")
        try:
            await asyncio.to_thread(
                self.recorder.write_float_waveform,
                audio_path,
                samples,
                sample_rate=self.config.sample_rate,
            )
            if stop_event.is_set():
                return {"status": "interrupted", "reason": "recording_interrupted"}
            result = await self._transcribe_audio_path(audio_path, stop_event=stop_event)
            if result.get("status") != "complete":
                return result
            transcript = str(result["transcript"])
            voice_match = dict(result["voice_match"])
            query = transcript
            if strip_wake_word:
                wake_query = _extract_wake_query(transcript, self.config.wake_word)
                query = wake_query if wake_query is not None else transcript
            query = str(query).strip()
            if not query:
                self.state.last_status = "empty_transcript"
                return {"status": "empty", "reason": "wake_word_only"}
            self.state.last_listen_transcript = query[:500]
            self.state.last_transcript = query
            self.state.wake_state = "responding"
            return await self._respond_to_transcript(
                transcript=query,
                session_id=self.state.session_id,
                stop_event=stop_event,
                voice_match=voice_match,
            )
        finally:
            self._cleanup(audio_path)

    async def _transcribe_captured_utterance(
        self,
        samples: list[float],
        *,
        stop_event: asyncio.Event,
    ) -> dict[str, Any]:
        audio_path = self._temporary_audio_path("terminal-utterance")
        try:
            await asyncio.to_thread(
                self.recorder.write_float_waveform,
                audio_path,
                samples,
                sample_rate=self.config.sample_rate,
            )
            if stop_event.is_set():
                return {"status": "interrupted", "reason": "recording_interrupted"}
            return await self._transcribe_audio_path(audio_path, stop_event=stop_event)
        finally:
            self._cleanup(audio_path)

    async def _transcribe_audio_path(
        self,
        audio_path: Path,
        *,
        stop_event: asyncio.Event,
    ) -> dict[str, Any]:
        voice_match = await self._verify_utterance_speaker(audio_path)
        if self.state.fingerprint_enabled and not voice_match.get("owner_voice_matched"):
            self.state.last_status = "rejected"
            return {"status": "rejected", "voice_match": voice_match}
        if stop_event.is_set():
            return {"status": "interrupted", "reason": "verification_interrupted"}

        self.state.last_status = "transcribing"
        transcript = str(await self.stt.transcribe(audio_path)).strip()
        if stop_event.is_set():
            return {"status": "interrupted", "reason": "transcription_interrupted"}
        if not transcript:
            self.state.last_status = "empty_transcript"
            return {"status": "empty", "reason": "empty_transcript"}
        self.state.last_transcript = transcript
        self.state.last_listen_transcript = transcript[:500]
        self.state.last_status = "complete"
        return {
            "status": "complete",
            "transcript": transcript,
            "voice_match": voice_match,
        }

    async def _verify_utterance_speaker(self, audio_path: Path) -> dict[str, Any]:
        if not self.state.fingerprint_enabled:
            self.state.owner_voice_matched = False
            self.state.last_fingerprint_reason = "fingerprint_disabled"
            self.state.last_fingerprint_similarity = None
            return {
                "owner_voice_matched": False,
                "verification_skipped": True,
                "reason": "fingerprint_disabled",
            }
        self.state.last_status = "matching_owner_voice"
        voice_match = await asyncio.to_thread(self.fingerprint.verify, audio_path)
        self.state.owner_voice_matched = bool(voice_match.get("owner_voice_matched"))
        self.state.last_fingerprint_reason = str(voice_match.get("reason") or "unknown")
        similarity = voice_match.get("similarity")
        self.state.last_fingerprint_similarity = (
            float(similarity) if isinstance(similarity, (int, float)) else None
        )
        return dict(voice_match)

    def _prepare_session(self, session_id: str) -> None:
        self.state.active = True
        self.state.session_id = str(session_id or "").strip() or f"voice-{uuid.uuid4()}"
        self.state.wake_state = "starting"
        self.state.last_status = "preparing_voice_models"
        self.state.last_error = ""
        self.state.interrupted = False
        self.state.owner_voice_matched = False
        self.state.last_fingerprint_reason = "not_checked"
        self.state.last_fingerprint_similarity = None
        self._stop_event = asyncio.Event()

    async def _respond_to_transcript(
        self,
        *,
        transcript: str,
        session_id: str,
        stop_event: asyncio.Event,
        voice_match: dict[str, Any],
    ) -> dict[str, Any]:
        if self.companion_callback is None:
            raise RuntimeError("companion callback is not configured")
        self.state.last_status = "thinking"
        reply = await self.companion_callback(text=transcript, session_id=session_id)
        if str(reply.get("status") or "") != "ok":
            return {"status": "companion_unavailable", "transcript": transcript, **reply}
        reply_text = str(reply.get("reply_text") or "").strip()
        self.state.last_reply = reply_text
        self.state.last_status = "speaking"
        speech_path = self._temporary_audio_path("reply", suffix=".mp3")
        try:
            try:
                await self.tts.synthesize(reply_text, speech_path)
                await self.player.play(speech_path, stop_event=stop_event)
            except Exception as exc:
                self.state.last_error = f"tts:{type(exc).__name__}: {exc}"
                return {
                    "status": "reply_ready_tts_unavailable",
                    "transcript": transcript,
                    "reply_text": reply_text,
                    "reason": self.state.last_error,
                }
            if stop_event.is_set():
                return {
                    "status": "interrupted",
                    "transcript": transcript,
                    "reply_text": reply_text,
                }
            self.state.last_status = "complete"
            return {
                "status": "complete",
                "session_id": session_id,
                "transcript": transcript,
                "reply_text": reply_text,
                "voice_match": voice_match,
            }
        finally:
            self._cleanup(speech_path)

    def _record_error(self, exc: Exception) -> dict[str, Any]:
        self.state.last_status = "error"
        self.state.last_error = f"{type(exc).__name__}: {exc}"
        return {"status": "error", "reason": self.state.last_error}

    def _fingerprint_status(self) -> str:
        if not self.state.fingerprint_enabled:
            return "disabled"
        return self.fingerprint.template_status

    @staticmethod
    def _temporary_audio_path(prefix: str, *, suffix: str = ".wav") -> Path:
        temp_dir = Path(tempfile.gettempdir()) / "voidcube-voice"
        temp_dir.mkdir(parents=True, exist_ok=True)
        return temp_dir / f"{prefix}-{uuid.uuid4()}{suffix}"

    def _cleanup(self, path: Path) -> None:
        if self.config.retain_raw_audio:
            return
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass

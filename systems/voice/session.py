from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
import tempfile
import time
from typing import Any, Awaitable, Callable
import uuid

from systems.voice.audio import AudioPlayer, AudioRecorder
from systems.voice.config import VoiceConfig
from systems.voice.fingerprint import FingerprintStore
from systems.voice.stt import SpeechToText
from systems.voice.tts import TextToSpeech


CompanionCallback = Callable[..., Awaitable[dict[str, Any]]]


@dataclass(slots=True)
class VoiceRuntimeState:
    enabled: bool = False
    active: bool = False
    continuous_active: bool = False
    wake_state: str = "idle"
    wake_word_hits: int = 0
    session_id: str = ""
    last_status: str = "idle"
    last_error: str = ""
    last_transcript: str = ""
    last_reply: str = ""
    owner_voice_matched: bool = False
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
        self.state = VoiceRuntimeState(enabled=self.config.enabled)
        self.recorder = AudioRecorder(
            sample_rate=self.config.sample_rate,
            channels=self.config.channels,
        )
        self.player = AudioPlayer()
        self.fingerprint = FingerprintStore(
            self.config.fingerprint_path,
            threshold=self.config.fingerprint_threshold,
        )
        self.stt = SpeechToText(
            base_url=self.config.stt_base_url,
            api_key=self.config.stt_api_key,
            model=self.config.stt_model,
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
            "session_id": self.state.session_id,
            "last_status": self.state.last_status,
            "last_error": self.state.last_error,
            "last_transcript": self.state.last_transcript,
            "last_reply": self.state.last_reply,
            "owner_voice_matched": self.state.owner_voice_matched,
            "interrupted": self.state.interrupted,
            "owner_voice_template_present": self.config.fingerprint_path.is_file(),
            "stt_configured": self.stt.configured,
            "tts_configured": self.tts.configured,
            "capture_available": self.recorder.available(),
            "playback_available": self.player.available(),
            "raw_audio_retained": self.config.retain_raw_audio,
        }

    def set_enabled(self, enabled: bool) -> dict[str, Any]:
        self.state.enabled = bool(enabled)
        if not enabled:
            self.interrupt()
        return self.status()

    def interrupt(self) -> dict[str, Any]:
        self._stop_event.set()
        self._continuous_stop_event.set()
        self.player.stop()
        task = self._continuous_task
        if task is not None and not task.done():
            task.cancel()
        self.state.interrupted = True
        return self.status()

    async def record_owner_template(
        self,
        *,
        duration_seconds: float = 5.0,
    ) -> dict[str, Any]:
        if self.state.continuous_active:
            return {"status": "busy", "reason": "continuous_listening_active"}
        async with self._lock:
            self.state.last_error = ""
            self.state.last_status = "recording_owner_voice_template"
            self._stop_event = asyncio.Event()
            path = self._temporary_audio_path("owner-template")
            try:
                await self.recorder.record(
                    path,
                    duration_seconds=min(duration_seconds, self.config.max_record_seconds),
                    stop_event=self._stop_event,
                )
                result = self.fingerprint.record_owner_template(path)
                self.state.last_status = "owner_voice_template_recorded"
                return {**result, "status": "owner_voice_template_recorded"}
            except Exception as exc:
                self.state.last_status = "error"
                self.state.last_error = f"{type(exc).__name__}: {exc}"
                return {"status": "error", "reason": self.state.last_error}
            finally:
                self._cleanup(path)

    async def run_once(
        self,
        *,
        session_id: str = "",
        duration_seconds: float = 8.0,
    ) -> dict[str, Any]:
        if not self.state.enabled:
            return {"status": "disabled", "reason": "voice_disabled"}
        if self.state.continuous_active:
            return {"status": "busy", "reason": "continuous_listening_active"}
        async with self._lock:
            self._prepare_session(session_id)
            try:
                capture = await self._capture_transcript(
                    duration_seconds=duration_seconds,
                    stop_event=self._stop_event,
                    prefix="input",
                    persist_transcript=True,
                )
                if capture.get("status") != "transcribed":
                    return capture
                return await self._respond_to_transcript(
                    transcript=str(capture["transcript"]),
                    session_id=self.state.session_id,
                    stop_event=self._stop_event,
                    voice_match=dict(capture.get("voice_match") or {}),
                )
            except asyncio.CancelledError:
                self.interrupt()
                raise
            except Exception as exc:
                return self._record_error(exc)
            finally:
                self.state.active = False

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
        self.state.wake_state = "listening"
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
        if was_running:
            self.state.last_status = "continuous_stopped"
        return {"status": "stopped" if was_running else "already_stopped", **self.status()}

    async def _continuous_loop(self) -> None:
        armed_until = 0.0
        self.state.continuous_active = True
        self.state.last_status = "continuous_listening"
        try:
            while self.state.enabled and not self._continuous_stop_event.is_set():
                self.state.active = True
                self.state.wake_state = "armed" if time.monotonic() < armed_until else "listening"
                self._stop_event = self._continuous_stop_event
                capture = await self._capture_transcript(
                    duration_seconds=self.config.continuous_segment_seconds,
                    stop_event=self._continuous_stop_event,
                    prefix="listen",
                    persist_transcript=False,
                )
                self.state.active = False
                if self._continuous_stop_event.is_set():
                    break
                if capture.get("status") != "transcribed":
                    await asyncio.sleep(0)
                    continue

                transcript = str(capture.get("transcript") or "").strip()
                query = transcript
                if self.config.wake_word_required:
                    wake_word = self.config.wake_word
                    wake_index = transcript.find(wake_word)
                    is_armed = time.monotonic() < armed_until
                    if wake_index >= 0:
                        self.state.wake_word_hits += 1
                        armed_until = time.monotonic() + self.config.wake_window_seconds
                        query = transcript[wake_index + len(wake_word):].strip(" ，,。.!！？?：:")
                    elif not is_armed:
                        self.state.last_status = "awaiting_wake_word"
                        self.state.wake_state = "listening"
                        continue
                    if not query:
                        self.state.last_status = "wake_word_detected"
                        self.state.wake_state = "armed"
                        continue

                self.state.wake_state = "responding"
                self.state.last_transcript = query
                self.state.active = True
                await self._respond_to_transcript(
                    transcript=query,
                    session_id=self.state.session_id,
                    stop_event=self._continuous_stop_event,
                    voice_match=dict(capture.get("voice_match") or {}),
                )
                self.state.active = False
                armed_until = 0.0
                if not self._continuous_stop_event.is_set():
                    self.state.wake_state = "listening"
                    self.state.last_status = "continuous_listening"
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._record_error(exc)
        finally:
            self.player.stop()
            self.state.active = False
            self.state.continuous_active = False
            self.state.wake_state = "idle"
            self._continuous_task = None

    def _prepare_session(self, session_id: str) -> None:
        self.state.active = True
        self.state.session_id = str(session_id or "").strip() or f"voice-{uuid.uuid4()}"
        self.state.last_status = "recording"
        self.state.last_error = ""
        self.state.interrupted = False
        self.state.owner_voice_matched = False
        self._stop_event = asyncio.Event()

    async def _capture_transcript(
        self,
        *,
        duration_seconds: float,
        stop_event: asyncio.Event,
        prefix: str,
        persist_transcript: bool,
    ) -> dict[str, Any]:
        audio_path = self._temporary_audio_path(prefix)
        try:
            self.state.last_status = "recording"
            await self.recorder.record(
                audio_path,
                duration_seconds=min(duration_seconds, self.config.max_record_seconds),
                stop_event=stop_event,
            )
            if stop_event.is_set():
                return {"status": "interrupted", "reason": "recording_interrupted"}
            self.state.last_status = "matching_owner_voice"
            voice_match = self.fingerprint.verify(audio_path)
            self.state.owner_voice_matched = bool(
                voice_match.get("owner_voice_matched")
            )
            if not self.state.owner_voice_matched:
                self.state.last_status = "rejected"
                return {"status": "rejected", "voice_match": voice_match}
            self.state.last_status = "transcribing"
            transcript = str(await self.stt.transcribe(audio_path)).strip()
            if persist_transcript:
                self.state.last_transcript = transcript
            if stop_event.is_set():
                return {"status": "interrupted", "reason": "transcription_interrupted"}
            if not transcript:
                self.state.last_status = "empty_transcript"
                return {"status": "empty", "reason": "empty_transcript"}
            return {
                "status": "transcribed",
                "transcript": transcript,
                "voice_match": voice_match,
            }
        finally:
            self._cleanup(audio_path)

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

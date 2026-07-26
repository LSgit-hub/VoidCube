from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
import tempfile
from pathlib import Path
import uuid
from typing import Any, Awaitable, Callable

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
    session_id: str = ""
    last_status: str = "idle"
    last_error: str = ""
    last_transcript: str = ""
    last_reply: str = ""
    authenticated: bool = False
    interrupted: bool = False


class VoiceSessionManager:
    """Voice transport with explicit auth, cancellation, and raw-audio cleanup."""

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
        self._lock = asyncio.Lock()

    def status(self) -> dict[str, Any]:
        return {
            "enabled": self.state.enabled,
            "active": self.state.active,
            "session_id": self.state.session_id,
            "last_status": self.state.last_status,
            "last_error": self.state.last_error,
            "last_transcript": self.state.last_transcript,
            "last_reply": self.state.last_reply,
            "authenticated": self.state.authenticated,
            "interrupted": self.state.interrupted,
            "fingerprint_enrolled": self.config.fingerprint_path.is_file(),
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
        self.player.stop()
        self.state.interrupted = True
        return self.status()

    async def enroll(self, *, duration_seconds: float = 5.0) -> dict[str, Any]:
        async with self._lock:
            self.state.last_error = ""
            self.state.last_status = "enrolling"
            self._stop_event = asyncio.Event()
            path = self._temporary_audio_path("enroll")
            try:
                await self.recorder.record(
                    path,
                    duration_seconds=min(duration_seconds, self.config.max_record_seconds),
                    stop_event=self._stop_event,
                )
                result = self.fingerprint.enroll(path)
                self.state.last_status = "enrolled"
                return {**result, "status": "enrolled"}
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
        async with self._lock:
            self.state.active = True
            self.state.session_id = str(session_id or "") or f"voice-{uuid.uuid4()}"
            self.state.last_status = "recording"
            self.state.last_error = ""
            self.state.interrupted = False
            self.state.authenticated = False
            self._stop_event = asyncio.Event()
            audio_path = self._temporary_audio_path("input")
            speech_path: Path | None = None
            try:
                await self.recorder.record(
                    audio_path,
                    duration_seconds=min(duration_seconds, self.config.max_record_seconds),
                    stop_event=self._stop_event,
                )
                if self._stop_event.is_set():
                    return {"status": "interrupted", "reason": "recording_interrupted"}
                self.state.last_status = "authenticating"
                auth = self.fingerprint.verify(audio_path)
                self.state.authenticated = bool(auth.get("authenticated"))
                if not self.state.authenticated:
                    self.state.last_status = "rejected"
                    return {"status": "rejected", "authentication": auth}
                self.state.last_status = "transcribing"
                transcript = await self.stt.transcribe(audio_path)
                self.state.last_transcript = transcript
                if self._stop_event.is_set():
                    return {"status": "interrupted", "reason": "transcription_interrupted"}
                if self.companion_callback is None:
                    raise RuntimeError("companion callback is not configured")
                self.state.last_status = "thinking"
                reply = await self.companion_callback(
                    text=transcript,
                    session_id=self.state.session_id,
                )
                if str(reply.get("status") or "") != "ok":
                    return {"status": "companion_unavailable", "transcript": transcript, **reply}
                reply_text = str(reply.get("reply_text") or "").strip()
                self.state.last_reply = reply_text
                self.state.last_status = "speaking"
                speech_path = self._temporary_audio_path("reply", suffix=".mp3")
                try:
                    await self.tts.synthesize(reply_text, speech_path)
                    await self.player.play(speech_path, stop_event=self._stop_event)
                except Exception as exc:
                    self.state.last_error = f"tts:{type(exc).__name__}: {exc}"
                    return {
                        "status": "reply_ready_tts_unavailable",
                        "transcript": transcript,
                        "reply_text": reply_text,
                        "reason": self.state.last_error,
                    }
                if self._stop_event.is_set():
                    return {
                        "status": "interrupted",
                        "transcript": transcript,
                        "reply_text": reply_text,
                    }
                self.state.last_status = "complete"
                return {
                    "status": "complete",
                    "session_id": self.state.session_id,
                    "transcript": transcript,
                    "reply_text": reply_text,
                    "authentication": auth,
                }
            except asyncio.CancelledError:
                self.interrupt()
                raise
            except Exception as exc:
                self.state.last_status = "error"
                self.state.last_error = f"{type(exc).__name__}: {exc}"
                return {"status": "error", "reason": self.state.last_error}
            finally:
                self.state.active = False
                self._cleanup(audio_path)
                if speech_path is not None:
                    self._cleanup(speech_path)

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

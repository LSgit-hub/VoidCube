"""Compatibility facade over the canonical Stellar voice transport."""

from __future__ import annotations

from pathlib import Path
import shutil
import tempfile
from typing import Any, Dict, Optional

from systems.voice.audio import AudioPlayer, AudioRecorder
from systems.voice.config import VoiceConfig
from systems.voice.stt import SpeechToText


def play_beep() -> None:
    try:
        import winsound

        winsound.MessageBeep()
    except (ImportError, RuntimeError):
        return


def create_audio_recorder(output_path: str) -> Optional[Any]:
    del output_path
    recorder = AudioRecorder()
    return recorder if recorder.available() else None


def check_voice_requirements() -> Dict[str, Any]:
    config = VoiceConfig.from_env()
    environment = detect_audio_environment()
    return {
        "available": bool(
            environment["capture_available"]
            and environment["playback_available"]
            and config.stt_base_url
        ),
        **environment,
        "stt_configured": bool(config.stt_base_url and config.stt_model),
        "fingerprint_path": str(config.fingerprint_path),
        "raw_audio_retained": config.retain_raw_audio,
    }


def transcribe_recording(audio_path: str) -> Optional[str]:
    config = VoiceConfig.from_env()
    client = SpeechToText(
        base_url=config.stt_base_url,
        api_key=config.stt_api_key,
        model=config.stt_model,
    )
    if not client.configured:
        return None
    return asyncio.run(client.transcribe(audio_path))


def detect_audio_environment() -> Dict[str, Any]:
    return {
        "capture_available": AudioRecorder.available(),
        "playback_available": AudioPlayer.available(),
        "ffplay_path": shutil.which("ffplay") or "",
    }


def cleanup_temp_recordings() -> None:
    directory = Path(tempfile.gettempdir()) / "voidcube-voice"
    if not directory.is_dir():
        return
    for path in directory.glob("*"):
        if path.is_file():
            path.unlink(missing_ok=True)

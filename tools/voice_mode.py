"""Compatibility facade over the canonical Stellar voice transport."""

from __future__ import annotations

import asyncio
from pathlib import Path
import queue
import shutil
import tempfile
from typing import Any, Dict, Optional

from systems.voice.audio import AudioPlayer, AudioRecorder
from systems.voice.config import VoiceConfig
from systems.voice.stt import SpeechToText
from systems.voice.tts import TextToSpeech


_PLAYER = AudioPlayer()


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


def play_audio_file(path: str) -> None:
    asyncio.run(_PLAYER.play(path, stop_event=asyncio.Event()))


def detect_audio_environment() -> Dict[str, Any]:
    return {
        "capture_available": AudioRecorder.available(),
        "playback_available": AudioPlayer.available(),
        "ffplay_path": shutil.which("ffplay") or "",
    }


def stop_playback() -> None:
    _PLAYER.stop()


def cleanup_temp_recordings() -> None:
    directory = Path(tempfile.gettempdir()) / "voidcube-voice"
    if not directory.is_dir():
        return
    for path in directory.glob("*"):
        if path.is_file():
            path.unlink(missing_ok=True)


def stream_tts_to_speaker(
    text_queue,
    stop_event,
    done_callback,
    display_callback=None,
) -> None:
    config = VoiceConfig.from_env()
    tts = TextToSpeech(
        provider=config.tts_provider,
        voice=config.tts_voice,
        base_url=config.tts_base_url,
        api_key=config.tts_api_key,
        model=config.tts_model,
    )

    async def run() -> None:
        while not stop_event.is_set():
            try:
                text = text_queue.get(timeout=0.1)
            except queue.Empty:
                await asyncio.sleep(0.05)
                continue
            if text is None:
                break
            value = str(text).strip()
            if not value:
                continue
            if display_callback:
                display_callback(value)
            output = Path(tempfile.gettempdir()) / "voidcube-voice" / "stream-reply.mp3"
            try:
                await tts.synthesize(value, output)
                bridge_stop = asyncio.Event()
                if stop_event.is_set():
                    bridge_stop.set()
                await _PLAYER.play(output, stop_event=bridge_stop)
            finally:
                output.unlink(missing_ok=True)

    try:
        asyncio.run(run())
    finally:
        if done_callback:
            done_callback()

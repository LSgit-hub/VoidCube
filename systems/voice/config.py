from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path


def _env_bool(name: str, default: bool) -> bool:
    value = str(os.getenv(name, "")).strip().lower()
    if not value:
        return default
    return value in {"1", "true", "yes", "on"}


@dataclass(frozen=True, slots=True)
class VoiceConfig:
    enabled: bool = False
    sample_rate: int = 16000
    channels: int = 1
    max_record_seconds: float = 12.0
    fingerprint_threshold: float = 0.86
    fingerprint_path: Path = Path("runtime/voice/fingerprint.json")
    stt_base_url: str = ""
    stt_api_key: str = ""
    stt_model: str = "whisper-1"
    tts_provider: str = "edge"
    tts_voice: str = "zh-CN-XiaoxiaoNeural"
    tts_base_url: str = ""
    tts_api_key: str = ""
    tts_model: str = "tts-1"
    retain_raw_audio: bool = False

    @classmethod
    def from_env(cls) -> "VoiceConfig":
        return cls(
            enabled=_env_bool("VOIDCUBE_VOICE_ENABLED", False),
            sample_rate=max(8000, int(os.getenv("VOIDCUBE_VOICE_SAMPLE_RATE", "16000"))),
            channels=1,
            max_record_seconds=max(
                1.0, float(os.getenv("VOIDCUBE_VOICE_MAX_RECORD_SECONDS", "12"))
            ),
            fingerprint_threshold=max(
                0.0,
                min(1.0, float(os.getenv("VOIDCUBE_VOICE_FINGERPRINT_THRESHOLD", "0.86"))),
            ),
            fingerprint_path=Path(
                os.getenv(
                    "VOIDCUBE_VOICE_FINGERPRINT_PATH",
                    "runtime/voice/fingerprint.json",
                )
            ),
            stt_base_url=str(os.getenv("VOIDCUBE_STT_BASE_URL", "")).rstrip("/"),
            stt_api_key=str(os.getenv("VOIDCUBE_STT_API_KEY", "")),
            stt_model=str(os.getenv("VOIDCUBE_STT_MODEL", "whisper-1")),
            tts_provider=str(os.getenv("VOIDCUBE_TTS_PROVIDER", "edge")).lower(),
            tts_voice=str(os.getenv("VOIDCUBE_TTS_VOICE", "zh-CN-XiaoxiaoNeural")),
            tts_base_url=str(os.getenv("VOIDCUBE_TTS_BASE_URL", "")).rstrip("/"),
            tts_api_key=str(os.getenv("VOIDCUBE_TTS_API_KEY", "")),
            tts_model=str(os.getenv("VOIDCUBE_TTS_MODEL", "tts-1")),
            retain_raw_audio=_env_bool("VOIDCUBE_VOICE_RETAIN_RAW_AUDIO", False),
        )

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
from typing import Any
import yaml


def _env_bool(name: str, default: bool) -> bool:
    value = str(os.getenv(name, "")).strip().lower()
    if not value:
        return default
    return value in {"1", "true", "yes", "on"}


def _canonical_stt_config() -> dict[str, Any]:
    """Read the canonical user config without making it a hard dependency."""
    try:
        from VoidCube_core.constants import get_config_path

        path = get_config_path()
        if path.is_file():
            raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            stt = raw.get("stt") or {}
            if isinstance(stt, dict):
                return stt
    except Exception:
        pass
    return {}


@dataclass(frozen=True, slots=True)
class VoiceConfig:
    enabled: bool = False
    sample_rate: int = 16000
    channels: int = 1
    max_record_seconds: float = 12.0
    wake_word: str = "你好，星子"
    wake_keyword_tokens: str = "n ǐ h ǎo x īng z ǐ @你好星子"
    wake_word_required: bool = True
    wake_cue_enabled: bool = True
    speech_start_timeout_seconds: float = 8.0
    speech_end_silence_seconds: float = 3.0
    max_utterance_seconds: float = 45.0
    vad_threshold: float = 0.5
    vad_model_path: Path = Path("runtime/voice/models/silero_vad.onnx")
    wake_model_root: Path = Path("runtime/voice/models")
    wake_threshold: float = 0.25
    wake_score: float = 1.5
    fingerprint_threshold: float = 0.5
    fingerprint_path: Path = Path("runtime/voice/fingerprint.json")
    fingerprint_model_path: Path = Path(
        "runtime/voice/models/"
        "3dspeaker_speech_campplus_sv_zh_en_16k-common_advanced.onnx"
    )
    fingerprint_enabled: bool = True
    stt_provider: str = "auto"
    stt_base_url: str = ""
    stt_api_key: str = ""
    stt_model: str = "base"
    stt_language: str = "zh"
    stt_hotwords: str = "你好 星子 西子 VoidCube 语音系统"
    stt_device: str = "cpu"
    stt_compute_type: str = "int8"
    tts_provider: str = "edge"
    tts_voice: str = "zh-CN-XiaoxiaoNeural"
    tts_base_url: str = ""
    tts_api_key: str = ""
    tts_model: str = "tts-1"
    retain_raw_audio: bool = False

    @classmethod
    def from_env(cls) -> "VoiceConfig":
        canonical_stt = _canonical_stt_config()
        canonical_local = canonical_stt.get("local") or {}
        if not isinstance(canonical_local, dict):
            canonical_local = {}
        canonical_enabled = bool(canonical_stt.get("enabled", False))
        stt_base_url = str(os.getenv("VOIDCUBE_STT_BASE_URL", "")).rstrip("/")
        stt_provider = str(
            os.getenv("VOIDCUBE_STT_PROVIDER", canonical_stt.get("provider", "auto"))
        ).strip().lower()
        remote_stt = stt_provider in {"remote", "openai", "openai_compatible"} or (
            stt_provider == "auto" and bool(stt_base_url)
        )
        return cls(
            enabled=_env_bool("VOIDCUBE_VOICE_ENABLED", canonical_enabled),
            sample_rate=max(8000, int(os.getenv("VOIDCUBE_VOICE_SAMPLE_RATE", "16000"))),
            channels=1,
            max_record_seconds=max(
                1.0, float(os.getenv("VOIDCUBE_VOICE_MAX_RECORD_SECONDS", "12"))
            ),
            wake_word=(
                str(os.getenv("VOIDCUBE_VOICE_WAKE_WORD", "你好，星子")).strip()
                or "你好，星子"
            ),
            wake_keyword_tokens=(
                str(
                    os.getenv(
                        "VOIDCUBE_VOICE_WAKE_KEYWORD_TOKENS",
                        "n ǐ h ǎo x īng z ǐ @你好星子",
                    )
                ).strip()
                or "n ǐ h ǎo x īng z ǐ @你好星子"
            ),
            wake_word_required=_env_bool("VOIDCUBE_VOICE_WAKE_WORD_REQUIRED", True),
            wake_cue_enabled=_env_bool("VOIDCUBE_VOICE_WAKE_CUE_ENABLED", True),
            speech_start_timeout_seconds=max(
                1.0,
                min(
                    30.0,
                    float(
                        os.getenv(
                            "VOIDCUBE_VOICE_SPEECH_START_TIMEOUT_SECONDS",
                            "8",
                        )
                    ),
                ),
            ),
            speech_end_silence_seconds=max(
                0.5,
                min(
                    5.0,
                    float(
                        os.getenv(
                            "VOIDCUBE_VOICE_SPEECH_END_SILENCE_SECONDS",
                            "3",
                        )
                    ),
                ),
            ),
            max_utterance_seconds=max(
                5.0,
                min(
                    120.0,
                    float(
                        os.getenv(
                            "VOIDCUBE_VOICE_MAX_UTTERANCE_SECONDS",
                            "45",
                        )
                    ),
                ),
            ),
            vad_threshold=max(
                0.0,
                min(1.0, float(os.getenv("VOIDCUBE_VOICE_VAD_THRESHOLD", "0.5"))),
            ),
            vad_model_path=Path(
                os.getenv(
                    "VOIDCUBE_VOICE_VAD_MODEL_PATH",
                    "runtime/voice/models/silero_vad.onnx",
                )
            ),
            wake_model_root=Path(
                os.getenv(
                    "VOIDCUBE_VOICE_WAKE_MODEL_ROOT",
                    "runtime/voice/models",
                )
            ),
            wake_threshold=max(
                0.0,
                min(
                    1.0,
                    float(os.getenv("VOIDCUBE_VOICE_WAKE_THRESHOLD", "0.25")),
                ),
            ),
            wake_score=max(
                0.1,
                min(10.0, float(os.getenv("VOIDCUBE_VOICE_WAKE_SCORE", "1.5"))),
            ),
            fingerprint_threshold=max(
                0.0,
                min(1.0, float(os.getenv("VOIDCUBE_VOICE_FINGERPRINT_THRESHOLD", "0.5"))),
            ),
            fingerprint_path=Path(
                os.getenv(
                    "VOIDCUBE_VOICE_FINGERPRINT_PATH",
                    "runtime/voice/fingerprint.json",
                )
            ),
            fingerprint_model_path=Path(
                os.getenv(
                    "VOIDCUBE_VOICE_FINGERPRINT_MODEL_PATH",
                    "runtime/voice/models/"
                    "3dspeaker_speech_campplus_sv_zh_en_16k-common_advanced.onnx",
                )
            ),
            fingerprint_enabled=_env_bool(
                "VOIDCUBE_VOICE_FINGERPRINT_ENABLED",
                True,
            ),
            stt_provider=stt_provider or "auto",
            stt_base_url=stt_base_url,
            stt_api_key=str(os.getenv("VOIDCUBE_STT_API_KEY", "")),
            stt_model=str(
                os.getenv(
                    "VOIDCUBE_STT_MODEL",
                    canonical_local.get("model")
                    if not remote_stt and canonical_local.get("model")
                    else "whisper-1" if remote_stt else "base",
                )
            ),
            stt_language=str(
                os.getenv("VOIDCUBE_STT_LANGUAGE", canonical_local.get("language", "zh"))
            ).strip() or "zh",
            stt_hotwords=str(
                os.getenv(
                    "VOIDCUBE_STT_HOTWORDS",
                    "你好 星子 西子 VoidCube 语音系统",
                )
            ).strip(),
            stt_device=str(os.getenv("VOIDCUBE_STT_DEVICE", "cpu")).strip() or "cpu",
            stt_compute_type=(
                str(os.getenv("VOIDCUBE_STT_COMPUTE_TYPE", "int8")).strip()
                or "int8"
            ),
            tts_provider=str(os.getenv("VOIDCUBE_TTS_PROVIDER", "edge")).lower(),
            tts_voice=str(os.getenv("VOIDCUBE_TTS_VOICE", "zh-CN-XiaoxiaoNeural")),
            tts_base_url=str(os.getenv("VOIDCUBE_TTS_BASE_URL", "")).rstrip("/"),
            tts_api_key=str(os.getenv("VOIDCUBE_TTS_API_KEY", "")),
            tts_model=str(os.getenv("VOIDCUBE_TTS_MODEL", "tts-1")),
            retain_raw_audio=_env_bool("VOIDCUBE_VOICE_RETAIN_RAW_AUDIO", False),
        )

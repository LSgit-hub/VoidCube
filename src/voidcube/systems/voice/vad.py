from __future__ import annotations

from pathlib import Path
import threading
from typing import Any

from .model_assets import download_verified


VAD_MODEL_NAME = "silero_vad.onnx"
VAD_MODEL_URL = (
    "https://github.com/k2-fsa/sherpa-onnx/releases/download/"
    f"asr-models/{VAD_MODEL_NAME}"
)
VAD_MODEL_SHA256 = (
    "9e2449e1087496d8d4caba907f23e0bd3f78d91fa552479bb9c23ac09cbb1fd6"
)


class SpeechEndpointDetector:
    """Silero VAD endpoint detector for complete, variable-length utterances."""

    def __init__(
        self,
        model_path: str | Path,
        *,
        sample_rate: int = 16000,
        threshold: float = 0.5,
        min_silence_seconds: float = 3.0,
        min_speech_seconds: float = 0.2,
        max_speech_seconds: float = 45.0,
    ) -> None:
        self.model_path = Path(model_path)
        self.sample_rate = int(sample_rate)
        self.threshold = float(threshold)
        self.min_silence_seconds = float(min_silence_seconds)
        self.min_speech_seconds = float(min_speech_seconds)
        self.max_speech_seconds = float(max_speech_seconds)
        self._vad: Any = None
        self._lock = threading.RLock()

    @property
    def model_ready(self) -> bool:
        return self.model_path.is_file()

    @property
    def speech_active(self) -> bool:
        return bool(self._vad is not None and self._vad.is_speech_detected())

    def ensure_model(self) -> Path:
        return download_verified(VAD_MODEL_URL, self.model_path, VAD_MODEL_SHA256)

    def reset(self) -> None:
        self._ensure_runtime()
        self._vad.reset()

    def accept(self, samples: Any) -> None:
        self._ensure_runtime()
        self._vad.accept_waveform(samples)

    def pop_utterance(self) -> list[float] | None:
        self._ensure_runtime()
        if self._vad.empty():
            return None
        segment = self._vad.front
        samples = [float(value) for value in segment.samples]
        self._vad.pop()
        return samples

    def _ensure_runtime(self) -> None:
        if self._vad is not None:
            return
        with self._lock:
            if self._vad is not None:
                return
            model_path = self.ensure_model()
            import sherpa_onnx

            silero = sherpa_onnx.SileroVadModelConfig(
                model=str(model_path),
                threshold=self.threshold,
                min_silence_duration=self.min_silence_seconds,
                min_speech_duration=self.min_speech_seconds,
                window_size=512,
                max_speech_duration=self.max_speech_seconds,
            )
            config = sherpa_onnx.VadModelConfig(
                silero_vad=silero,
                sample_rate=self.sample_rate,
                num_threads=1,
                provider="cpu",
            )
            if not config.validate():
                raise RuntimeError("Silero VAD model configuration is invalid")
            self._vad = sherpa_onnx.VoiceActivityDetector(
                config,
                buffer_size_in_seconds=self.max_speech_seconds + 10.0,
            )

from __future__ import annotations

from pathlib import Path
import threading
from typing import Any

from systems.voice.model_assets import download_verified, extract_verified_tar


KWS_MODEL_NAME = (
    "sherpa-onnx-kws-zipformer-wenetspeech-3.3M-2024-01-01-mobile"
)
KWS_ARCHIVE_NAME = f"{KWS_MODEL_NAME}.tar.bz2"
KWS_MODEL_URL = (
    "https://github.com/k2-fsa/sherpa-onnx/releases/download/kws-models/"
    f"{KWS_ARCHIVE_NAME}"
)
KWS_MODEL_SHA256 = (
    "b812a043aef628a6915f89cb9a94e55f8e87e89ff904b516f822d7e0a3e6de2b"
)
KWS_ENCODER = "encoder-epoch-12-avg-2-chunk-16-left-64.int8.onnx"
KWS_DECODER = "decoder-epoch-12-avg-2-chunk-16-left-64.onnx"
KWS_JOINER = "joiner-epoch-12-avg-2-chunk-16-left-64.int8.onnx"


class WakeWordDetector:
    """Streaming local keyword detector backed by sherpa-onnx."""

    def __init__(
        self,
        model_root: str | Path,
        *,
        keyword_tokens: str,
        sample_rate: int = 16000,
        threshold: float = 0.25,
        score: float = 1.5,
    ) -> None:
        self.model_root = Path(model_root)
        self.model_dir = self.model_root / KWS_MODEL_NAME
        self.archive_path = self.model_root / KWS_ARCHIVE_NAME
        self.keyword_tokens = str(keyword_tokens).strip()
        self.sample_rate = int(sample_rate)
        self.threshold = float(threshold)
        self.score = float(score)
        self._spotter: Any = None
        self._stream: Any = None
        self._lock = threading.RLock()

    @property
    def model_ready(self) -> bool:
        return all(path.is_file() for path in self._required_paths())

    def ensure_model(self) -> Path:
        with self._lock:
            if not self.model_ready:
                archive = download_verified(
                    KWS_MODEL_URL,
                    self.archive_path,
                    KWS_MODEL_SHA256,
                )
                extract_verified_tar(archive, self.model_dir)
            missing = [str(path) for path in self._required_paths() if not path.is_file()]
            if missing:
                raise RuntimeError(f"Keyword model files are missing: {missing}")
            return self.model_dir

    def reset(self) -> None:
        self._ensure_runtime()
        self._stream = self._spotter.create_stream()

    def accept(self, samples: Any) -> str:
        self._ensure_runtime()
        if self._stream is None:
            self._stream = self._spotter.create_stream()
        self._stream.accept_waveform(self.sample_rate, samples)
        while self._spotter.is_ready(self._stream):
            self._spotter.decode_stream(self._stream)
            result = self._spotter.get_result(self._stream)
            if result:
                self._spotter.reset_stream(self._stream)
                return str(result).strip()
        return ""

    def _ensure_runtime(self) -> None:
        if self._spotter is not None:
            return
        with self._lock:
            if self._spotter is not None:
                return
            model_dir = self.ensure_model()
            keyword_file = model_dir / "voidcube-keywords.txt"
            keyword_file.write_text(f"{self.keyword_tokens}\n", encoding="utf-8")
            import sherpa_onnx

            self._spotter = sherpa_onnx.KeywordSpotter(
                tokens=str(model_dir / "tokens.txt"),
                encoder=str(model_dir / KWS_ENCODER),
                decoder=str(model_dir / KWS_DECODER),
                joiner=str(model_dir / KWS_JOINER),
                keywords_file=str(keyword_file),
                num_threads=2,
                sample_rate=self.sample_rate,
                keywords_score=self.score,
                keywords_threshold=self.threshold,
                num_trailing_blanks=1,
                provider="cpu",
            )

    def _required_paths(self) -> tuple[Path, ...]:
        return (
            self.model_dir / "tokens.txt",
            self.model_dir / KWS_ENCODER,
            self.model_dir / KWS_DECODER,
            self.model_dir / KWS_JOINER,
        )

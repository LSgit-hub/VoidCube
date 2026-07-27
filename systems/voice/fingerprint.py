from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import threading
from typing import Any, Iterable

import httpx


SPEAKER_ENGINE = "sherpa-onnx-campplus"
SPEAKER_MODEL_NAME = (
    "3dspeaker_speech_campplus_sv_zh_en_16k-common_advanced.onnx"
)
SPEAKER_MODEL_URL = (
    "https://github.com/k2-fsa/sherpa-onnx/releases/download/"
    f"speaker-recongition-models/{SPEAKER_MODEL_NAME}"
)
SPEAKER_MODEL_SHA256 = (
    "aa3cfc16963a10586a9393f5035d6d6b57e98d358b347f80c2a30bf4f00ceba2"
)


class FingerprintStore:
    """Persist local speaker embeddings without retaining enrollment audio."""

    def __init__(
        self,
        path: str | Path,
        *,
        threshold: float = 0.5,
        model_path: str | Path = Path("runtime/voice/models") / SPEAKER_MODEL_NAME,
    ) -> None:
        self.path = Path(path)
        self.threshold = threshold
        self.model_path = Path(model_path)
        self._extractor: Any = None
        self._model_lock = threading.RLock()

    @property
    def template_status(self) -> str:
        if not self.path.is_file():
            return "missing"
        try:
            payload = self._read_payload()
        except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
            return "invalid"
        if int(payload.get("version") or 0) != 2:
            return "upgrade_required"
        if str(payload.get("engine") or "") != SPEAKER_ENGINE:
            return "upgrade_required"
        embeddings = payload.get("embeddings")
        if not isinstance(embeddings, list) or len(embeddings) < 2:
            return "invalid"
        return "enrolled"

    @property
    def model_ready(self) -> bool:
        return self.model_path.is_file()

    def record_owner_templates(
        self,
        audio_paths: Iterable[str | Path],
    ) -> dict[str, int | str]:
        paths = [Path(path) for path in audio_paths]
        if len(paths) < 2:
            raise ValueError("At least two enrollment recordings are required")
        embeddings = [self._extract_embedding(path) for path in paths]
        dimension = len(embeddings[0])
        if dimension <= 0 or any(len(embedding) != dimension for embedding in embeddings):
            raise ValueError("Speaker embeddings have inconsistent dimensions")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(
                {
                    "version": 2,
                    "engine": SPEAKER_ENGINE,
                    "model": SPEAKER_MODEL_NAME,
                    "model_sha256": SPEAKER_MODEL_SHA256,
                    "embedding_dimension": dimension,
                    "embeddings": embeddings,
                    "enrollment_sample_count": len(embeddings),
                    "created_at": datetime.now(timezone.utc).isoformat(),
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        return {
            "status": "owner_voice_template_recorded",
            "path": str(self.path),
            "engine": SPEAKER_ENGINE,
            "embedding_dimension": dimension,
            "enrollment_sample_count": len(embeddings),
        }

    def verify(self, audio_path: str | Path) -> dict[str, float | bool | str]:
        template_status = self.template_status
        if template_status == "missing":
            return self._rejection("owner_voice_template_missing")
        if template_status == "upgrade_required":
            return self._rejection("owner_voice_template_upgrade_required")
        if template_status != "enrolled":
            return self._rejection("owner_voice_template_invalid")
        try:
            payload = self._read_payload()
            embeddings = [
                [float(value) for value in embedding]
                for embedding in payload["embeddings"]
            ]
            actual = self._extract_embedding(Path(audio_path))
            import sherpa_onnx

            manager = sherpa_onnx.SpeakerEmbeddingManager(len(actual))
            if not manager.add("owner", embeddings):
                raise ValueError("Unable to register owner speaker embeddings")
            similarity = float(manager.score("owner", actual))
        except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
            return self._rejection(
                f"owner_voice_template_read_failed:{type(exc).__name__}"
            )
        matched = similarity >= self.threshold
        return {
            "owner_voice_matched": matched,
            "reason": "matched" if matched else "owner_voice_mismatch",
            "similarity": round(similarity, 6),
            "threshold": self.threshold,
            "engine": SPEAKER_ENGINE,
        }

    def ensure_model(self) -> Path:
        with self._model_lock:
            if self.model_path.is_file():
                self._verify_model_hash(self.model_path)
                return self.model_path

            self.model_path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.model_path.with_suffix(
                f"{self.model_path.suffix}.part-{os.getpid()}"
            )
            try:
                import truststore

                truststore.inject_into_ssl()
                with httpx.stream(
                    "GET",
                    SPEAKER_MODEL_URL,
                    follow_redirects=True,
                    timeout=180.0,
                ) as response:
                    response.raise_for_status()
                    with temporary.open("wb") as output:
                        for chunk in response.iter_bytes():
                            output.write(chunk)
                self._verify_model_hash(temporary)
                temporary.replace(self.model_path)
            finally:
                temporary.unlink(missing_ok=True)
            return self.model_path

    def _get_extractor(self) -> Any:
        if self._extractor is not None:
            return self._extractor
        with self._model_lock:
            if self._extractor is not None:
                return self._extractor
            model_path = self.ensure_model()
            import sherpa_onnx

            config = sherpa_onnx.SpeakerEmbeddingExtractorConfig(
                model=str(model_path),
                num_threads=2,
                provider="cpu",
            )
            if not config.validate():
                raise RuntimeError("Speaker embedding model configuration is invalid")
            self._extractor = sherpa_onnx.SpeakerEmbeddingExtractor(config)
            return self._extractor

    def _extract_embedding(self, audio_path: Path) -> list[float]:
        import soundfile as sf

        audio, sample_rate = sf.read(
            str(audio_path),
            dtype="float32",
            always_2d=False,
        )
        if getattr(audio, "ndim", 1) > 1:
            audio = audio.mean(axis=1)
        if getattr(audio, "size", 0) <= 0:
            raise ValueError("Audio recording is empty")
        extractor = self._get_extractor()
        stream = extractor.create_stream()
        stream.accept_waveform(int(sample_rate), audio.tolist())
        stream.input_finished()
        if not extractor.is_ready(stream):
            raise ValueError("Audio recording is too short for speaker verification")
        embedding = [float(value) for value in extractor.compute(stream)]
        if not embedding:
            raise ValueError("Speaker embedding is empty")
        return embedding

    def _read_payload(self) -> dict[str, Any]:
        payload = json.loads(self.path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("Speaker template must be a JSON object")
        return payload

    @staticmethod
    def _rejection(reason: str) -> dict[str, float | bool | str]:
        return {
            "owner_voice_matched": False,
            "reason": reason,
            "similarity": 0.0,
        }

    @staticmethod
    def _file_sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as source:
            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    def _verify_model_hash(self, path: Path) -> None:
        actual = self._file_sha256(path)
        if actual != SPEAKER_MODEL_SHA256:
            raise RuntimeError(
                "Speaker embedding model checksum mismatch: "
                f"expected {SPEAKER_MODEL_SHA256}, got {actual}"
            )

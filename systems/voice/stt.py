from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import httpx


class SpeechToText:
    def __init__(
        self,
        *,
        provider: str = "auto",
        base_url: str,
        api_key: str,
        model: str,
        language: str = "zh",
        hotwords: str = "",
        device: str = "cpu",
        compute_type: str = "int8",
        timeout: float = 30.0,
    ) -> None:
        requested_provider = str(provider or "auto").strip().lower()
        self.provider = (
            "remote"
            if requested_provider in {"remote", "openai", "openai_compatible"}
            or (requested_provider == "auto" and bool(base_url))
            else "local"
        )
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.language = str(language or "").strip() or None
        self.hotwords = str(hotwords or "").strip() or None
        self.device = str(device or "cpu").strip() or "cpu"
        self.compute_type = str(compute_type or "int8").strip() or "int8"
        self.timeout = timeout
        self._local_model: Any = None

    @property
    def configured(self) -> bool:
        if self.provider == "remote":
            return bool(self.base_url and self.model)
        try:
            import faster_whisper  # noqa: F401
        except ImportError:
            return False
        return bool(self.model)

    async def transcribe(self, audio_path: str | Path) -> str:
        if not self.configured:
            raise RuntimeError("STT is not configured")
        if self.provider == "local":
            return await asyncio.to_thread(self._transcribe_local, Path(audio_path))

        headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            with Path(audio_path).open("rb") as audio_file:
                response = await client.post(
                    f"{self.base_url}/audio/transcriptions",
                    headers=headers,
                    data={"model": self.model},
                    files={"file": (Path(audio_path).name, audio_file, "audio/wav")},
                )
            response.raise_for_status()
            payload: Any = response.json()
        text = str(payload.get("text") if isinstance(payload, dict) else payload).strip()
        return text

    def _transcribe_local(self, audio_path: Path) -> str:
        if self._local_model is None:
            import truststore
            from faster_whisper import WhisperModel

            # Model downloads must trust the host OS certificate store, including
            # locally managed HTTPS roots, without disabling TLS verification.
            truststore.inject_into_ssl()
            self._local_model = WhisperModel(
                self.model,
                device=self.device,
                compute_type=self.compute_type,
            )
        segments, _ = self._local_model.transcribe(
            str(audio_path),
            language=self.language,
            beam_size=3,
            vad_filter=True,
            hotwords=self.hotwords,
        )
        text = "".join(str(segment.text) for segment in segments).strip()
        return text

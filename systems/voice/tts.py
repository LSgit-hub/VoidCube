from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import httpx


class TextToSpeech:
    def __init__(
        self,
        *,
        provider: str,
        voice: str,
        base_url: str,
        api_key: str,
        model: str,
        timeout: float = 30.0,
    ) -> None:
        self.provider = provider
        self.voice = voice
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.timeout = timeout

    @property
    def configured(self) -> bool:
        return self.provider == "edge" or bool(self.base_url and self.model)

    async def synthesize(self, text: str, output_path: str | Path) -> Path:
        if not self.configured:
            raise RuntimeError("TTS is not configured")
        path = Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        if self.provider == "edge":
            try:
                import edge_tts
            except ImportError as exc:
                raise RuntimeError("edge-tts is not installed") from exc
            communicator = edge_tts.Communicate(str(text), self.voice)
            await communicator.save(str(path))
            return path
        headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(
                f"{self.base_url}/audio/speech",
                headers=headers,
                json={
                    "model": self.model,
                    "input": str(text),
                    "voice": self.voice,
                    "response_format": "wav",
                },
            )
            response.raise_for_status()
            path.write_bytes(response.content)
        return path

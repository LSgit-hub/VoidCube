from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx


class SpeechToText:
    def __init__(self, *, base_url: str, api_key: str, model: str, timeout: float = 30.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.timeout = timeout

    @property
    def configured(self) -> bool:
        return bool(self.base_url and self.model)

    async def transcribe(self, audio_path: str | Path) -> str:
        if not self.configured:
            raise RuntimeError("STT is not configured")
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
        if not text:
            raise RuntimeError("STT returned empty text")
        return text

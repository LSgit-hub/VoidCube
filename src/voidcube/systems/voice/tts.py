from __future__ import annotations

import asyncio
import re
from pathlib import Path
from typing import Any

import httpx


_MARKDOWN_LINK = re.compile(r"!\[([^]]*)\]\([^)]*\)|\[([^]]+)\]\([^)]*\)")
_URL = re.compile(r"(?:https?://|www\.)[^\s)]+", re.IGNORECASE)
_CODE_BLOCK = re.compile(r"```(?:[A-Za-z0-9_+#.-]+)?\s*.*?```", re.DOTALL)
_INLINE_CODE = re.compile(r"`([^`]+)`")
_HEADING = re.compile(r"(?m)^\s{0,3}#{1,6}\s*")
_LIST = re.compile(r"(?m)^\s*(?:[-*+]\s+|\d+[.)]\s+)")


def sanitize_speech_text(text: str) -> str:
    """Turn display Markdown into natural speech without changing display text."""
    value = str(text or "").replace("\\", "")
    value = _CODE_BLOCK.sub("代码内容已省略。", value)
    value = _INLINE_CODE.sub(r"\1", value)
    value = _MARKDOWN_LINK.sub(lambda match: match.group(1) or match.group(2) or "链接", value)
    value = _URL.sub("链接", value)
    value = _HEADING.sub("", value)
    value = _LIST.sub("", value)
    value = re.sub(r"(?:\*\*|__|~~|===|---)", "", value)
    value = value.replace("|", " ")
    for symbol, spoken in {
        "@": " 艾特 ", "%": " 百分之 ", "&": " 和 ", "*": " 乘 ",
        "+": " 加 ", "=": " 等于 ", "÷": " 除以 ", "×": " 乘以 ",
        "→": " 到 ",
    }.items():
        value = value.replace(symbol, spoken)
    value = re.sub(r"\s-\s", " 减 ", value)
    value = re.sub(r"\s/\s", " 除以 ", value)
    value = re.sub(r"[ \t]+", " ", value)
    value = re.sub(r"\n{3,}", "\n\n", value)
    return value.strip()


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
        speech_text = sanitize_speech_text(text)
        if not speech_text:
            raise ValueError("TTS text is empty after speech sanitization")
        path.parent.mkdir(parents=True, exist_ok=True)
        if self.provider == "edge":
            try:
                import edge_tts
            except ImportError as exc:
                raise RuntimeError("edge-tts is not installed") from exc
            communicator = edge_tts.Communicate(speech_text, self.voice)
            await communicator.save(str(path))
            return path
        headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(
                f"{self.base_url}/audio/speech",
                headers=headers,
                json={
                    "model": self.model,
                    "input": speech_text,
                    "voice": self.voice,
                    "response_format": "wav",
                },
            )
            response.raise_for_status()
            path.write_bytes(response.content)
        return path

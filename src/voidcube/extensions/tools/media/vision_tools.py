"""Unified image analysis tool backed by the auxiliary vision router."""

from __future__ import annotations

import base64
import json
import mimetypes
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from ..registry import registry, tool_error

_RESIZE_TARGET_BYTES = 20 * 1024 * 1024
_MAX_IMAGE_BYTES = 25 * 1024 * 1024

VISION_SCHEMA = {
    "description": "Analyze one or more local images or image URLs with the configured vision model.",
    "parameters": {
        "type": "object",
        "properties": {
            "images": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Local image paths or HTTP(S) image URLs.",
            },
            "image_path": {
                "type": "string",
                "description": "Legacy single-image path or URL.",
            },
            "prompt": {
                "type": "string",
                "description": "Question or analysis instruction for the image.",
            },
            "detail": {
                "type": "string",
                "enum": ["low", "high", "auto"],
                "default": "auto",
            },
            "max_tokens": {
                "type": "integer",
                "minimum": 50,
                "maximum": 4096,
                "default": 1024,
            },
        },
        "required": ["prompt"],
    },
}


def _is_image_size_error(error: Exception) -> bool:
    text = str(error).lower()
    return any(marker in text for marker in ("image too large", "payload too large", "413", "maximum image"))


def _resize_image_for_vision(image_path: str | Path, mime_type: str = "image/jpeg") -> str:
    """Resize an image below the auxiliary provider payload limit."""
    try:
        from io import BytesIO
        from PIL import Image

        with Image.open(image_path) as image:
            image = image.convert("RGB")
            image.thumbnail((2048, 2048))
            output = BytesIO()
            image.save(output, format="JPEG", quality=82, optimize=True)
            data = output.getvalue()
    except Exception as exc:
        raise RuntimeError(f"Unable to resize image: {exc}") from exc
    if len(data) > _RESIZE_TARGET_BYTES:
        raise RuntimeError("Image remains too large after resizing")
    return f"data:image/jpeg;base64,{base64.b64encode(data).decode('ascii')}"


def _load_image_data(source: str) -> tuple[str, str]:
    parsed = urlparse(source)
    if parsed.scheme in {"http", "https"}:
        request = Request(source, headers={"User-Agent": "VoidCube/vision"})
        with urlopen(request, timeout=20) as response:
            data = response.read(_MAX_IMAGE_BYTES + 1)
            content_type = str(response.headers.get_content_type() or "")
        if len(data) > _MAX_IMAGE_BYTES:
            raise ValueError("image exceeds the 25 MB limit")
        mime = content_type if content_type.startswith("image/") else mimetypes.guess_type(source)[0] or "image/jpeg"
    else:
        path = Path(source).expanduser().resolve()
        data = path.read_bytes()
        if len(data) > _MAX_IMAGE_BYTES:
            return _resize_image_for_vision(path), "image/jpeg"
        mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    if not mime.startswith("image/"):
        raise ValueError(f"not an image source: {source}")
    return f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}", mime


def _vision_backends_available() -> bool:
    try:
        from ....infrastructure.providers.auxiliary_client import get_available_vision_backends

        return bool(get_available_vision_backends())
    except Exception:
        return False


def _vision_backends_configured() -> bool:
    """Return local configuration status for startup tool gating.

    Runtime calls still use ``_vision_backends_available`` so a stale or
    unreachable provider is reported at the point of use, not during startup.
    """
    try:
        from ....infrastructure.providers.auxiliary_client import get_configured_vision_backends

        return bool(get_configured_vision_backends())
    except Exception:
        return False


def vision_analyze_tool(
    images: Optional[list[str]] = None,
    prompt: str = "",
    image_path: str = "",
    detail: str = "auto",
    max_tokens: int = 1024,
    image_url: str = "",
    user_prompt: str = "",
    **kwargs: Any,
) -> str:
    del kwargs
    if image_url.strip() and image_url.strip() not in (images or []):
        image_path = image_path or image_url
    if user_prompt.strip() and not prompt.strip():
        prompt = user_prompt
    sources = [str(item).strip() for item in (images or []) if str(item).strip()]
    if image_path.strip() and image_path.strip() not in sources:
        sources.insert(0, image_path.strip())
    if not sources:
        return tool_error("vision_analyze requires images or image_path")
    if not str(prompt or "").strip():
        return tool_error("vision_analyze requires prompt")
    if not _vision_backends_available():
        return tool_error("No configured vision backend is available", available=False)

    try:
        content = [{"type": "text", "text": str(prompt).strip()}]
        for source in sources[:10]:
            data_url, _mime = _load_image_data(source)
            content.append({"type": "image_url", "image_url": {"url": data_url, "detail": detail}})
        from ....infrastructure.providers.auxiliary_client import call_llm, extract_content_or_reasoning
        response = call_llm(
            task="vision",
            messages=[{"role": "user", "content": content}],
            max_tokens=max(50, min(4096, int(max_tokens))),
            temperature=0.1,
            timeout=120,
        )
        text = extract_content_or_reasoning(response).strip()
        try:
            from ....infrastructure.persistence.redaction import redact_sensitive_text
            text = redact_sensitive_text(text)
        except Exception:
            pass
        return json.dumps({"success": True, "analysis": text, "images": sources[:10]}, ensure_ascii=False)
    except Exception as exc:
        return tool_error(f"Vision analysis failed: {type(exc).__name__}: {exc}")


registry.register(
    name="vision_analyze",
    toolset="vision",
    schema=VISION_SCHEMA,
    handler=lambda args, **kwargs: vision_analyze_tool(**args, **kwargs),
    check_fn=_vision_backends_configured,
    effect="read_only",
)

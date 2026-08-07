"""Resolve the dedicated multimodal provider independently from API-A/API-B."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping
from urllib.parse import urlsplit, urlunsplit

from VoidCube_app.provider_auth import normalize_openai_compatible_base_url


AGNES_PROVIDER_ID = "agnes-ai"
AGNES_API_KEY_ENV = "AGNES_API_KEY"
AGNES_BASE_URL = "https://api.agnes-ai.cn/v1"
AGNES_LANGUAGE_MODEL = "agnes-2.5-flash"
AGNES_IMAGE_MODEL = "agnes-image-2.1-flash"
AGNES_VIDEO_MODEL = "agnes-video-v2.0"


@dataclass(frozen=True, slots=True)
class MultimodalProviderConfig:
    provider: str
    api_key_env: str
    api_key: str
    base_url: str
    language_model: str
    image_model: str
    video_model: str
    chat_completions_path: str
    image_generations_path: str
    image_edits_path: str
    videos_path: str
    video_result_path: str
    request_timeout_seconds: float
    video_poll_interval_seconds: float
    video_timeout_seconds: float

    def endpoint(self, path: str) -> str:
        return f"{self.base_url.rstrip('/')}/{str(path or '').lstrip('/')}"

    def origin_endpoint(self, path: str) -> str:
        """Resolve a provider path that lives outside the versioned API root."""
        parsed = urlsplit(self.base_url)
        root = urlunsplit((parsed.scheme, parsed.netloc, "", "", "")).rstrip("/")
        return f"{root}/{str(path or '').lstrip('/')}"

    @property
    def configured(self) -> bool:
        if self.provider != AGNES_PROVIDER_ID:
            return False
        try:
            from VoidCube_app.provider_auth import has_usable_secret

            return has_usable_secret(self.api_key)
        except Exception:
            return bool(self.api_key)


def default_multimodal_config() -> dict[str, Any]:
    return {
        "provider": AGNES_PROVIDER_ID,
        "api_key_env": AGNES_API_KEY_ENV,
        "base_url": AGNES_BASE_URL,
        "language_model": AGNES_LANGUAGE_MODEL,
        "image_model": AGNES_IMAGE_MODEL,
        "video_model": AGNES_VIDEO_MODEL,
        "chat_completions_path": "/chat/completions",
        "image_generations_path": "/images/generations",
        "image_edits_path": "/images/edits",
        "videos_path": "/videos",
        "video_result_path": "/agnesapi",
        "request_timeout_seconds": 120,
        "video_poll_interval_seconds": 3,
        "video_timeout_seconds": 600,
    }


def resolve_multimodal_provider(
    config: Mapping[str, Any] | None = None,
) -> MultimodalProviderConfig:
    if config is None:
        from VoidCube_app.config import load_config

        config = load_config()
    raw = config.get("multimodal") if isinstance(config, Mapping) else None
    values = default_multimodal_config()
    if isinstance(raw, Mapping):
        values.update({key: value for key, value in raw.items() if value is not None})

    api_key_env = str(values.get("api_key_env") or AGNES_API_KEY_ENV).strip()
    api_key = ""
    if api_key_env:
        try:
            from VoidCube_app.config import get_env_value

            api_key = str(get_env_value(api_key_env) or "").strip()
        except Exception:
            api_key = ""

    return MultimodalProviderConfig(
        provider=str(values.get("provider") or "").strip().lower(),
        api_key_env=api_key_env,
        api_key=api_key,
        base_url=normalize_openai_compatible_base_url(
            str(values.get("base_url") or AGNES_BASE_URL)
        ),
        language_model=str(values.get("language_model") or AGNES_LANGUAGE_MODEL).strip(),
        image_model=str(values.get("image_model") or AGNES_IMAGE_MODEL).strip(),
        video_model=str(values.get("video_model") or AGNES_VIDEO_MODEL).strip(),
        chat_completions_path=str(
            values.get("chat_completions_path") or "/chat/completions"
        ).strip(),
        image_generations_path=str(
            values.get("image_generations_path") or "/images/generations"
        ).strip(),
        image_edits_path=str(values.get("image_edits_path") or "/images/edits").strip(),
        videos_path=str(values.get("videos_path") or "/videos").strip(),
        video_result_path=str(values.get("video_result_path") or "/agnesapi").strip(),
        request_timeout_seconds=float(values.get("request_timeout_seconds") or 120),
        video_poll_interval_seconds=float(
            values.get("video_poll_interval_seconds") or 3
        ),
        video_timeout_seconds=float(values.get("video_timeout_seconds") or 600),
    )


def multimodal_provider_configured() -> bool:
    return resolve_multimodal_provider().configured


__all__ = [
    "AGNES_API_KEY_ENV",
    "AGNES_BASE_URL",
    "AGNES_IMAGE_MODEL",
    "AGNES_LANGUAGE_MODEL",
    "AGNES_PROVIDER_ID",
    "AGNES_VIDEO_MODEL",
    "MultimodalProviderConfig",
    "default_multimodal_config",
    "multimodal_provider_configured",
    "resolve_multimodal_provider",
]

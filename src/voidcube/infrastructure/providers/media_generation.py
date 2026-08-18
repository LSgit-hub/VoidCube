"""Resolve independent Agnes-AI image and video generation routes."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping


AGNES_PROVIDER_ID = "agnes-ai"
AGNES_API_KEY_ENV = "AGNES_API_KEY"
AGNES_IMAGE_ENDPOINT = "https://api.agnes-ai.cn/v1/images/generations"
AGNES_IMAGE_EDIT_ENDPOINT = "https://api.agnes-ai.cn/v1/images/edits"
AGNES_IMAGE_MODEL = "agnes-image-2.1-flash"
AGNES_VIDEO_ENDPOINT = "https://api.agnes-ai.cn/v1/videos"
AGNES_VIDEO_RESULT_ENDPOINT = "https://api.agnes-ai.cn/agnesapi"
AGNES_VIDEO_MODEL = "agnes-video-v2.0"


def _api_key(api_key_env: str) -> str:
    try:
        from ..config.configuration import get_env_value

        return str(get_env_value(api_key_env) or "").strip()
    except Exception:
        return ""


def _usable_secret(value: str) -> bool:
    try:
        from .auth import has_usable_secret

        return has_usable_secret(value)
    except Exception:
        return bool(value)


@dataclass(frozen=True, slots=True)
class ImageGenerationConfig:
    provider: str
    api_key_env: str
    api_key: str
    endpoint: str
    edit_endpoint: str
    model: str
    request_timeout_seconds: float

    @property
    def configured(self) -> bool:
        return (
            self.provider == AGNES_PROVIDER_ID
            and self.endpoint.startswith(("http://", "https://"))
            and bool(self.model)
            and _usable_secret(self.api_key)
        )


@dataclass(frozen=True, slots=True)
class VideoGenerationConfig:
    provider: str
    api_key_env: str
    api_key: str
    endpoint: str
    result_endpoint: str
    model: str
    request_timeout_seconds: float
    poll_interval_seconds: float
    timeout_seconds: float

    @property
    def configured(self) -> bool:
        return (
            self.provider == AGNES_PROVIDER_ID
            and self.endpoint.startswith(("http://", "https://"))
            and self.result_endpoint.startswith(("http://", "https://"))
            and bool(self.model)
            and _usable_secret(self.api_key)
        )


def default_image_generation_config() -> dict[str, Any]:
    return {
        "provider": AGNES_PROVIDER_ID,
        "api_key_env": AGNES_API_KEY_ENV,
        "endpoint": AGNES_IMAGE_ENDPOINT,
        "edit_endpoint": AGNES_IMAGE_EDIT_ENDPOINT,
        "model": AGNES_IMAGE_MODEL,
        "request_timeout_seconds": 120,
    }


def default_video_generation_config() -> dict[str, Any]:
    return {
        "provider": AGNES_PROVIDER_ID,
        "api_key_env": AGNES_API_KEY_ENV,
        "endpoint": AGNES_VIDEO_ENDPOINT,
        "result_endpoint": AGNES_VIDEO_RESULT_ENDPOINT,
        "model": AGNES_VIDEO_MODEL,
        "request_timeout_seconds": 120,
        "poll_interval_seconds": 3,
        "timeout_seconds": 600,
    }


def _section(config: Mapping[str, Any] | None, name: str, defaults: dict[str, Any]) -> dict[str, Any]:
    if config is None:
        from ..config.configuration import load_config

        config = load_config()
    values = dict(defaults)
    raw = config.get(name) if isinstance(config, Mapping) else None
    if isinstance(raw, Mapping):
        values.update({key: value for key, value in raw.items() if value is not None})
    return values


def resolve_image_generation_config(
    config: Mapping[str, Any] | None = None,
) -> ImageGenerationConfig:
    values = _section(config, "image_generation", default_image_generation_config())
    api_key_env = str(values.get("api_key_env") or AGNES_API_KEY_ENV).strip()
    return ImageGenerationConfig(
        provider=str(values.get("provider") or "").strip().lower(),
        api_key_env=api_key_env,
        api_key=_api_key(api_key_env),
        endpoint=str(values.get("endpoint") or AGNES_IMAGE_ENDPOINT).strip(),
        edit_endpoint=str(values.get("edit_endpoint") or AGNES_IMAGE_EDIT_ENDPOINT).strip(),
        model=str(values.get("model") or AGNES_IMAGE_MODEL).strip(),
        request_timeout_seconds=float(values.get("request_timeout_seconds") or 120),
    )


def resolve_video_generation_config(
    config: Mapping[str, Any] | None = None,
) -> VideoGenerationConfig:
    values = _section(config, "video_generation", default_video_generation_config())
    api_key_env = str(values.get("api_key_env") or AGNES_API_KEY_ENV).strip()
    return VideoGenerationConfig(
        provider=str(values.get("provider") or "").strip().lower(),
        api_key_env=api_key_env,
        api_key=_api_key(api_key_env),
        endpoint=str(values.get("endpoint") or AGNES_VIDEO_ENDPOINT).strip(),
        result_endpoint=str(
            values.get("result_endpoint") or AGNES_VIDEO_RESULT_ENDPOINT
        ).strip(),
        model=str(values.get("model") or AGNES_VIDEO_MODEL).strip(),
        request_timeout_seconds=float(values.get("request_timeout_seconds") or 120),
        poll_interval_seconds=float(values.get("poll_interval_seconds") or 3),
        timeout_seconds=float(values.get("timeout_seconds") or 600),
    )


def image_generation_configured() -> bool:
    return resolve_image_generation_config().configured


def video_generation_configured() -> bool:
    return resolve_video_generation_config().configured


__all__ = [
    "AGNES_API_KEY_ENV",
    "AGNES_IMAGE_EDIT_ENDPOINT",
    "AGNES_IMAGE_ENDPOINT",
    "AGNES_IMAGE_MODEL",
    "AGNES_PROVIDER_ID",
    "AGNES_VIDEO_ENDPOINT",
    "AGNES_VIDEO_MODEL",
    "AGNES_VIDEO_RESULT_ENDPOINT",
    "ImageGenerationConfig",
    "VideoGenerationConfig",
    "default_image_generation_config",
    "default_video_generation_config",
    "image_generation_configured",
    "resolve_image_generation_config",
    "resolve_video_generation_config",
    "video_generation_configured",
]

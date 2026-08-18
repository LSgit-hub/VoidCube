"""OpenAI-compatible client construction primitives for provider adapters."""

from __future__ import annotations

from typing import Any

from openai import AsyncOpenAI, OpenAI


def normalize_base_url(base_url: str) -> str:
    return str(base_url or "").strip().rstrip("/")


def first_live_model(api_key: str, base_url: str) -> str | None:
    """Probe an OpenAI-compatible /models endpoint without static fallback."""
    try:
        from .model_catalog import fetch_api_models

        models = fetch_api_models(api_key, base_url)
    except Exception:
        return None
    return models[0] if models else None


def create_openai_client(
    api_key: str,
    base_url: str,
    *,
    default_headers: dict[str, str] | None = None,
) -> OpenAI:
    kwargs: dict[str, Any] = {"api_key": api_key, "base_url": normalize_base_url(base_url)}
    if default_headers:
        kwargs["default_headers"] = dict(default_headers)
    return OpenAI(**kwargs)


def create_async_openai_client(
    api_key: str,
    base_url: str,
    model: str | None,
    *,
    default_headers: dict[str, str] | None = None,
) -> tuple[AsyncOpenAI, str | None]:
    """Create the async counterpart with the same endpoint policy."""
    kwargs: dict[str, Any] = {
        "api_key": api_key,
        "base_url": normalize_base_url(base_url),
    }
    if default_headers:
        kwargs["default_headers"] = dict(default_headers)
    return AsyncOpenAI(**kwargs), model


__all__ = [
    "create_async_openai_client",
    "create_openai_client",
    "first_live_model",
    "normalize_base_url",
]

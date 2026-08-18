"""Vision backend selection policy, independent of client construction."""

from __future__ import annotations

from typing import Any, Callable, Iterable


VISION_AUTO_PROVIDER_ORDER = ("openrouter", "nous")


def configured_backends(
    *,
    requested: str,
    base_url: str | None,
    main_provider: str,
    provider_configured: Callable[[str], bool],
    normalize_provider: Callable[[str | None], str],
    candidates: Iterable[str] = VISION_AUTO_PROVIDER_ORDER,
) -> list[str]:
    configured: list[str] = []
    if base_url:
        return ["custom"]
    if requested not in ("auto", "") and provider_configured(requested):
        return [normalize_provider(requested)]
    if requested in ("auto", ""):
        if main_provider and main_provider not in ("auto", "") and provider_configured(main_provider):
            configured.append(normalize_provider(main_provider))
        for candidate in candidates:
            if candidate not in configured and provider_configured(candidate):
                configured.append(candidate)
        if provider_configured("custom") and "custom" not in configured:
            configured.append("custom")
    return configured


def available_backends(
    *,
    main_provider: str,
    main_model: str,
    strict_available: Callable[[str], bool],
    resolve_provider_client: Callable[..., tuple[Any, str | None]],
    candidates: Iterable[str] = VISION_AUTO_PROVIDER_ORDER,
) -> list[str]:
    available: list[str] = []
    if main_provider and main_provider not in ("auto", ""):
        if main_provider in candidates:
            if strict_available(main_provider):
                available.append(main_provider)
        elif resolve_provider_client(main_provider, main_model)[0] is not None:
            available.append(main_provider)
    for provider in candidates:
        if provider not in available and strict_available(provider):
            available.append(provider)
    return available


__all__ = ["VISION_AUTO_PROVIDER_ORDER", "available_backends", "configured_backends"]

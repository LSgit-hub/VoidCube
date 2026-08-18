"""Pure policy helpers for auxiliary LLM routing.

Network clients and terminal presentation stay outside this module.  These
helpers define provider aliases, task override precedence, and fallback error
classification so auxiliary consumers share one policy.
"""

from __future__ import annotations

from typing import Any, Mapping


PROVIDER_ALIASES: dict[str, str] = {
    "glm": "zai",
    "z-ai": "zai",
    "z.ai": "zai",
    "zhipu": "zai",
    "kimi": "kimi-coding",
    "moonshot": "kimi-coding",
    "minimax-china": "minimax-cn",
    "minimax_cn": "minimax-cn",
}


def normalize_auxiliary_provider(provider: str | None, *, for_vision: bool = False) -> str:
    normalized = str(provider or "auto").strip().lower()
    if normalized.startswith("custom:"):
        suffix = normalized.split(":", 1)[1].strip()
        normalized = suffix if suffix and not for_vision else "custom"
    return PROVIDER_ALIASES.get(normalized, normalized)


def normalize_main_runtime(main_runtime: Mapping[str, Any] | None) -> dict[str, str]:
    if not isinstance(main_runtime, Mapping):
        return {}
    normalized: dict[str, str] = {}
    for field in ("provider", "model", "base_url", "api_key"):
        value = main_runtime.get(field)
        if isinstance(value, str) and value.strip():
            normalized[field] = value.strip()
    if "provider" in normalized:
        normalized["provider"] = normalized["provider"].lower()
    return normalized


def is_payment_error(error: Exception) -> bool:
    status = getattr(error, "status_code", None)
    if status == 402:
        return True
    text = str(error).lower()
    return status in (402, 429, None) and any(
        marker in text
        for marker in ("credits", "insufficient funds", "can only afford", "billing", "payment required")
    )


def is_connection_error(error: Exception) -> bool:
    from openai import APIConnectionError, APITimeoutError

    if isinstance(error, (APIConnectionError, APITimeoutError)):
        return True
    error_type = type(error).__name__
    if any(marker in error_type for marker in ("Connection", "Timeout", "DNS", "SSL")):
        return True
    text = str(error).lower()
    return any(
        marker in text
        for marker in (
            "connection refused",
            "name or service not known",
            "no route to host",
            "network is unreachable",
            "timed out",
            "connection reset",
        )
    )


def fallback_reason(error: Exception, requested_provider: str | None) -> str | None:
    if requested_provider not in ("auto", "", None):
        return None
    if is_payment_error(error):
        return "payment error"
    if is_connection_error(error):
        return "connection error"
    return None


def resolve_task_provider_model(
    task: str | None = None,
    provider: str | None = None,
    model: str | None = None,
    base_url: str | None = None,
    api_key: str | None = None,
    *,
    config_loader: Any = None,
) -> tuple[str, str | None, str | None, str | None]:
    """Resolve explicit arguments, then ``auxiliary.<task>`` config overrides."""
    config: dict[str, Any] = {}
    if task:
        if config_loader is None:
            try:
                from ..config.configuration import load_config
                config_loader = load_config
            except ImportError:
                config_loader = None
        try:
            loaded = config_loader() if config_loader is not None else {}
            config = loaded if isinstance(loaded, dict) else {}
        except Exception:
            config = {}
    aux = config.get("auxiliary", {}) if isinstance(config.get("auxiliary"), dict) else {}
    task_config = aux.get(task, {}) if isinstance(aux, dict) and task else {}
    if not isinstance(task_config, dict):
        task_config = {}
    cfg_provider = str(task_config.get("provider", "")).strip() or None
    cfg_model = str(task_config.get("model", "")).strip() or None
    cfg_base_url = str(task_config.get("base_url", "")).strip() or None
    cfg_api_key = str(task_config.get("api_key", "")).strip() or None
    resolved_model = model or cfg_model

    if base_url:
        return "custom", resolved_model, base_url, api_key
    if provider:
        return provider, resolved_model, base_url, api_key
    if task:
        if cfg_base_url:
            return "custom", resolved_model, cfg_base_url, cfg_api_key
        if cfg_provider and cfg_provider != "auto":
            return cfg_provider, resolved_model, None, None
        return "auto", resolved_model, None, None
    return "auto", resolved_model, None, None


def normalize_vision_provider(provider: str | None) -> str:
    normalized = normalize_auxiliary_provider(provider, for_vision=True)
    if normalized in {"openai-compatible", "openai_compatible", "openai-compatible-api"}:
        return "custom"
    return normalized


__all__ = [
    "PROVIDER_ALIASES",
    "fallback_reason",
    "is_connection_error",
    "is_payment_error",
    "normalize_auxiliary_provider",
    "normalize_main_runtime",
    "normalize_vision_provider",
    "resolve_task_provider_model",
]

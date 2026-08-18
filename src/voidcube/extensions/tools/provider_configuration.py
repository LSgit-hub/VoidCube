"""Provider-aware tool setup policy without terminal dependencies."""

from __future__ import annotations

from typing import Any, Callable, Iterable, Mapping


def visible_providers(
    category: Mapping[str, Any],
    *,
    features: Any,
    managed_tools_enabled: Callable[[], bool],
) -> list[dict[str, Any]]:
    """Filter provider choices using subscription and local capability state."""
    visible: list[dict[str, Any]] = []
    for provider in category.get("providers", []):
        if provider.get("managed_nous_feature") and not managed_tools_enabled():
            continue
        if provider.get("requires_nous_auth") and not getattr(features, "nous_auth_present", False):
            continue
        visible.append(dict(provider))
    return visible


def is_provider_active(
    provider: Mapping[str, Any],
    config: Mapping[str, Any],
    *,
    features: Any,
) -> bool:
    """Return whether a provider matches the current durable config."""
    managed_feature = provider.get("managed_nous_feature")
    if managed_feature:
        feature = getattr(features, "features", {}).get(managed_feature)
        if feature is None:
            return False
        managed = bool(getattr(feature, "managed_by_nous", False))
        if provider.get("tts_provider"):
            return managed and config.get("tts", {}).get("provider") == provider["tts_provider"]
        if "browser_provider" in provider:
            return managed and config.get("browser", {}).get("cloud_provider") == provider["browser_provider"]
        if provider.get("web_backend"):
            return managed and config.get("web", {}).get("backend") == provider["web_backend"]
        return managed

    if provider.get("tts_provider"):
        return config.get("tts", {}).get("provider") == provider["tts_provider"]
    if "browser_provider" in provider:
        return config.get("browser", {}).get("cloud_provider") == provider["browser_provider"]
    if provider.get("web_backend"):
        return config.get("web", {}).get("backend") == provider["web_backend"]
    return False


def detect_active_provider_index(
    providers: Iterable[Mapping[str, Any]],
    config: Mapping[str, Any],
    *,
    features: Any,
    get_env_value: Callable[[str], str | None],
) -> int:
    """Choose the current provider, falling back to the first configured one."""
    entries = list(providers)
    for index, provider in enumerate(entries):
        if is_provider_active(provider, config, features=features):
            return index
        env_vars = provider.get("env_vars", [])
        if env_vars and all(get_env_value(item["key"]) for item in env_vars):
            return index
    return 0


def needs_configuration_prompt(
    toolset: str,
    config: Mapping[str, Any],
    *,
    categories: Mapping[str, Any],
    has_keys: Callable[[str, Mapping[str, Any]], bool],
) -> bool:
    """Determine whether enabling a toolset should open provider setup."""
    category = categories.get(toolset)
    if not category:
        return not has_keys(toolset, config)
    if toolset == "tts":
        return not isinstance(config.get("tts"), dict) or "provider" not in config.get("tts", {})
    if toolset == "web":
        return not isinstance(config.get("web"), dict) or "backend" not in config.get("web", {})
    if toolset == "browser":
        return not isinstance(config.get("browser"), dict) or "cloud_provider" not in config.get("browser", {})
    return not has_keys(toolset, config)


__all__ = [
    "detect_active_provider_index",
    "is_provider_active",
    "needs_configuration_prompt",
    "visible_providers",
]

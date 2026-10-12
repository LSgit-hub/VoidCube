"""Config-driven custom request headers for OpenAI-compatible providers.

Some endpoints require headers the OpenAI SDK never sends on its own. Rather
than growing one hard-coded branch per provider, the header template lives in
provider configuration so any provider can declare its own:

.. code-block:: yaml

    providers:
      step5:
        base_url: https://opencode.ai/zen/go/v1
        headers:
          x-opencode-session: "{session_id}"
          User-Agent: "my-agent/1.0"

``{session_id}`` expands to the live conversation id.  A header that still
contains the placeholder after expansion is dropped, because sending the
literal text is rejected by the providers that require it.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ...version import __version__

SESSION_ID_PLACEHOLDER = "{session_id}"

# Credentials and framing stay owned by the SDK; configuration may not
# override them even when a provider documents a custom value.
_RESERVED_HEADERS = frozenset({"authorization", "content-type", "content-length"})

# provider key -> (config mtime at read time, resolved template)
_USER_TEMPLATE_CACHE: dict[str, tuple[int | None, dict[str, str]]] = {}

def normalize_header_template(value: Any) -> dict[str, str]:
    """Return a validated header template, dropping unusable entries."""
    if not isinstance(value, Mapping):
        return {}
    template: dict[str, str] = {}
    for name, raw in value.items():
        header = str(name or "").strip()
        if not header or header.lower() in _RESERVED_HEADERS:
            continue
        template[header] = "" if raw is None else str(raw)
    return template


def resolve_header_template(
    template: Mapping[str, str] | None,
    *,
    session_id: str = "",
    client_id: str = "",
    request_id: str = "",
    platform: str = "",
    machine: str = "",
) -> dict[str, str]:
    """Expand request context and drop headers left unresolved."""
    values = {
        SESSION_ID_PLACEHOLDER: session_id or "",
        "{client_id}": client_id or "",
        "{request_id}": request_id or "",
        "{platform}": platform or "",
        "{machine}": machine or "",
        "{client_version}": __version__,
    }
    resolved: dict[str, str] = {}
    for name, value in (template or {}).items():
        expanded = str(value)
        for placeholder, replacement in values.items():
            expanded = expanded.replace(placeholder, replacement)
        if any(token in expanded for token in values) or not expanded:
            continue
        resolved[name] = expanded
    return resolved


def provider_header_template(provider_key: str) -> dict[str, str]:
    """Return the configured header template for one provider.

    Built-in registry entries win over user configuration so a provider's
    required headers survive a stale or hand-edited config file.

    The user-configuration lookup is cached per provider and invalidated by
    the config file's mtime: this runs on every chat request, and re-parsing
    ``config.yaml`` each time would put a full YAML load on the hot path.
    """
    from .registry import PROVIDER_HEADER_TEMPLATES, PROVIDER_REGISTRY

    key = str(provider_key or "").strip().lower()
    if not key:
        return {}

    registry_entry = PROVIDER_REGISTRY.get(key)
    if isinstance(registry_entry, Mapping):
        template = normalize_header_template(registry_entry.get("headers"))
        if template:
            return template

    template = normalize_header_template(PROVIDER_HEADER_TEMPLATES.get(key))
    if template:
        return template

    return _cached_user_header_template(key)


def _cached_user_header_template(key: str) -> dict[str, str]:
    """Return one provider's configured template, cached until config changes."""
    from ..config.runtime_paths import get_config_path

    try:
        mtime: int | None = get_config_path().stat().st_mtime_ns
    except OSError:
        # No readable config yet.  Bypass the cache rather than storing a
        # ``None`` stamp, or a config file created later in this process would
        # never be picked up.
        return _read_user_header_template(key)

    cached = _USER_TEMPLATE_CACHE.get(key)
    if cached is not None and cached[0] == mtime:
        return dict(cached[1])

    template = _read_user_header_template(key)
    _USER_TEMPLATE_CACHE[key] = (mtime, template)
    return dict(template)


def _read_user_header_template(key: str) -> dict[str, str]:
    from ..config.configuration import load_config

    try:
        config = load_config()
    except Exception:
        return {}
    providers = config.get("providers")
    if not isinstance(providers, dict):
        return {}
    entry = providers.get(key)
    if not isinstance(entry, dict):
        return {}
    return normalize_header_template(entry.get("headers"))


def builtin_headers_for_base_url(base_url: str) -> dict[str, str]:
    """Return headers required by a built-in endpoint, keyed by its base URL.

    Used where only the endpoint is known — client bootstrap and auxiliary
    routing resolve credentials before a provider key is settled.  Endpoints
    listed here work without any user configuration; a provider's own
    ``headers`` entry still overrides them.
    """
    from .registry import PROVIDER_REGISTRY

    normalized = (base_url or "").lower()
    if not normalized:
        return {}
    for key in ("openrouter", "kimi-coding"):
        entry = PROVIDER_REGISTRY.get(key)
        if not isinstance(entry, Mapping):
            continue
        endpoint = str(entry.get("base_url") or "").lower()
        if endpoint and endpoint in normalized:
            return normalize_header_template(entry.get("headers"))
    return {}


def resolve_provider_headers(
    provider_key: str = "",
    *,
    base_url: str = "",
    template: Mapping[str, str] | None = None,
    session_id: str = "",
    client_id: str = "",
    request_id: str = "",
) -> dict[str, str]:
    """Resolve one provider's configured headers for any request operation.

    ``template`` is used for a caller-provided override.  Otherwise the
    provider registry/configuration is authoritative; ``base_url`` is only a
    compatibility lookup for built-in providers whose key is unavailable.
    """
    selected = normalize_header_template(template) if template else {}
    if not selected:
        selected = provider_header_template(provider_key)
    if not selected and base_url:
        selected = builtin_headers_for_base_url(base_url)
    import platform

    return resolve_header_template(
        selected,
        session_id=session_id,
        client_id=client_id,
        request_id=request_id,
        platform=platform.system().lower(),
        machine=platform.machine(),
    )


__all__ = [
    "SESSION_ID_PLACEHOLDER",
    "builtin_headers_for_base_url",
    "normalize_header_template",
    "provider_header_template",
    "resolve_provider_headers",
    "resolve_header_template",
]

"""Shared runtime provider resolution for CLI, gateway, and helpers."""

from __future__ import annotations

import logging
import os
import re
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

from . import auth as auth_mod
from .credential_pool import CredentialPool, PooledCredential, get_custom_provider_pool_key, load_pool
from .registry import (
    LOCAL_RUNTIME_PROVIDER_IDS,
    PROVIDER_REGISTRY,
    RUNTIME_PROVIDER_IDS,
)
from .auth import (
    AuthError,
    DEFAULT_QWEN_BASE_URL,
    _agent_key_is_usable,
    format_auth_error,
    resolve_provider,
    resolve_nous_runtime_credentials,
    resolve_qwen_runtime_credentials,
    resolve_api_key_provider_credentials,
    resolve_external_process_provider_credentials,
    has_usable_secret,
    normalize_openai_compatible_base_url,
)
from ..config.configuration import load_config
from ..config.configuration import get_active_model_config
from .endpoints import OPENROUTER_BASE_URL
from ..shared.value_helpers import env_str, env_int


def _normalize_custom_provider_name(value: str) -> str:
    return value.strip().lower().replace(" ", "-")


def _auto_detect_local_model(base_url: str) -> str:
    """Query a local server for its model name when only one model is loaded."""
    if not base_url:
        return ""
    try:
        import requests
        url = base_url.rstrip("/")
        if not url.endswith("/v1"):
            url += "/v1"
        resp = requests.get(url + "/models", timeout=5)
        if resp.ok:
            models = resp.json().get("data", [])
            if len(models) == 1:
                model_id = models[0].get("id", "")
                if model_id:
                    return model_id
    except Exception:
        pass
    return ""


def _get_model_config() -> Dict[str, Any]:
    cfg = get_active_model_config(load_config())
    default = str(cfg.get("default") or "").strip()
    base_url = str(cfg.get("base_url") or "").strip()
    is_local = "localhost" in base_url or "127.0.0.1" in base_url
    is_fallback = not default
    if is_local and is_fallback and base_url:
        detected = _auto_detect_local_model(base_url)
        if detected:
            cfg["default"] = detected
            cfg["model"] = detected
    return cfg


def _resolve_runtime_from_pool_entry(
    *,
    provider: str,
    entry: PooledCredential,
    requested_provider: str,
    model_cfg: Optional[Dict[str, Any]] = None,
    pool: Optional[CredentialPool] = None,
) -> Dict[str, Any]:
    model_cfg = model_cfg or _get_model_config()
    base_url = (getattr(entry, "runtime_base_url", None) or getattr(entry, "base_url", None) or "").rstrip("/")
    api_key = getattr(entry, "runtime_api_key", None) or getattr(entry, "access_token", "")
    if provider == "qwen-oauth":
        base_url = base_url or DEFAULT_QWEN_BASE_URL
    elif provider == "openrouter":
        base_url = base_url or OPENROUTER_BASE_URL
    elif provider not in {"nous", "copilot"}:
        configured_provider = str(model_cfg.get("provider") or "").strip().lower()
        # Honour the active provider's saved base_url when the configured
        # provider matches this provider. Only override when the pool entry has no explicit base_url
        # (i.e. it fell back to the hardcoded default). Env var overrides win
        # (#6039).
        pconfig = PROVIDER_REGISTRY.get(provider)
        pool_url_is_default = pconfig and base_url.rstrip("/") == pconfig.inference_base_url.rstrip("/")
        if configured_provider == provider and pool_url_is_default:
            cfg_base_url = str(model_cfg.get("base_url") or "").strip().rstrip("/")
            if cfg_base_url:
                base_url = cfg_base_url
    return {
        "provider": provider,
        "base_url": base_url,
        "api_key": api_key,
        "source": getattr(entry, "source", "pool"),
        "credential_pool": pool,
        "requested_provider": requested_provider,
    }


def resolve_requested_provider(requested: Optional[str] = None) -> str:
    """Resolve provider request from explicit arg, then saved config."""
    if requested and requested.strip():
        return requested.strip().lower()

    config = load_config()
    runtime_cfg = config.get("runtime") if isinstance(config.get("runtime"), dict) else {}
    active_provider = runtime_cfg.get("active_provider")
    if isinstance(active_provider, str) and active_provider.strip():
        return active_provider.strip().lower()

    model_cfg = _get_model_config()
    cfg_provider = model_cfg.get("provider")
    if isinstance(cfg_provider, str) and cfg_provider.strip():
        return cfg_provider.strip().lower()

    return ""


def _try_resolve_from_custom_pool(
    base_url: str,
    provider_label: str,
) -> Optional[Dict[str, Any]]:
    """Check if a credential pool exists for a custom endpoint and return a runtime dict if so."""
    pool_key = get_custom_provider_pool_key(base_url)
    if not pool_key:
        return None
    try:
        pool = load_pool(pool_key)
        if not pool.has_credentials():
            return None
        entry = pool.select()
        if entry is None:
            return None
        pool_api_key = getattr(entry, "runtime_api_key", None) or getattr(entry, "access_token", "")
        if not pool_api_key:
            return None
        return {
            "provider": provider_label,
            "base_url": base_url,
            "api_key": pool_api_key,
            "source": f"pool:{pool_key}",
            "credential_pool": pool,
        }
    except Exception:
        return None


def _get_named_custom_provider(requested_provider: str) -> Optional[Dict[str, Any]]:
    requested_norm = _normalize_custom_provider_name(requested_provider or "")
    if not requested_norm or requested_norm == "custom":
        return None

    # Raw names should only map to custom providers when they are not already
    # valid built-in providers or aliases. Explicit menu keys like
    # ``custom:local`` always target the saved custom provider.
    if requested_norm == "auto":
        return None
    if not requested_norm.startswith("custom:") and requested_norm in PROVIDER_REGISTRY:
        return None

    config = load_config()
    providers = config.get("providers")
    if not isinstance(providers, dict):
        return None

    for provider_key, entry in providers.items():
        if not isinstance(entry, dict):
            continue
        name = entry.get("label") or entry.get("name") or provider_key
        base_url = entry.get("base_url") or entry.get("api") or entry.get("url")
        if not isinstance(name, str) or not isinstance(base_url, str):
            continue
        name_norm = _normalize_custom_provider_name(name)
        menu_key = f"custom:{name_norm}"
        provider_key_norm = _normalize_custom_provider_name(str(provider_key))
        if requested_norm not in {name_norm, menu_key, provider_key_norm}:
            continue
        result = {
            "name": name.strip(),
            "base_url": base_url.strip(),
            "api_key": str(entry.get("api_key", "") or "").strip(),
            "api_key_env": str(entry.get("api_key_env", "") or "").strip(),
            "auth_mode": str(entry.get("auth_mode", "") or "").strip().lower(),
        }
        model_name = str(entry.get("selected_model") or entry.get("default_model") or entry.get("model", "") or "").strip()
        if model_name:
            result["model"] = model_name
        return result

    return None


def _configured_provider_overrides(requested_provider: str) -> tuple[str, str]:
    """Return the endpoint and credential owned by one named pool entry."""
    requested_norm = _normalize_custom_provider_name(requested_provider or "")
    if not requested_norm:
        return "", ""
    config = load_config()
    providers = config.get("providers")
    if not isinstance(providers, dict):
        return "", ""
    entry = providers.get(requested_norm)
    if not isinstance(entry, dict):
        return "", ""

    base_url = normalize_openai_compatible_base_url(
        str(entry.get("base_url") or "").strip()
    )
    api_key = str(entry.get("api_key") or "").strip()
    api_key_env = str(entry.get("api_key_env") or "").strip()
    if not has_usable_secret(api_key) and api_key_env:
        from ..config.configuration import get_env_value

        api_key = str(get_env_value(api_key_env) or "").strip()
    if str(entry.get("auth_mode") or "").strip().lower() == "none":
        api_key = "no-key-required"
    return base_url, api_key if has_usable_secret(api_key) else ""


def _resolve_named_custom_runtime(
    *,
    requested_provider: str,
    explicit_api_key: Optional[str] = None,
    explicit_base_url: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    custom_provider = _get_named_custom_provider(requested_provider)
    if not custom_provider:
        return None

    base_url = (
        (explicit_base_url or "").strip()
        or custom_provider.get("base_url", "")
    )
    base_url = normalize_openai_compatible_base_url(base_url)
    if not base_url:
        return None

    # Check if a credential pool exists for this custom endpoint
    pool_result = _try_resolve_from_custom_pool(
        base_url,
        _normalize_custom_provider_name(requested_provider),
    )
    if pool_result:
        # Propagate the model name even when using pooled credentials.
        model_name = custom_provider.get("model")
        if model_name:
            pool_result["model"] = model_name
        return pool_result

    api_key_env = str(custom_provider.get("api_key_env") or "").strip()
    env_api_key = ""
    if api_key_env:
        from ..config.configuration import get_env_value

        env_api_key = str(get_env_value(api_key_env) or "").strip()
    api_key_candidates = [
        (explicit_api_key or "").strip(),
        str(custom_provider.get("api_key", "") or "").strip(),
        env_api_key,
    ]
    api_key = next((candidate for candidate in api_key_candidates if has_usable_secret(candidate)), "")
    auth_mode = str(custom_provider.get("auth_mode") or "").strip().lower()
    if not api_key and not api_key_env and not auth_mode:
        api_key = next(
            (
                candidate
                for candidate in (env_str("OPENAI_API_KEY"), env_str("OPENROUTER_API_KEY"))
                if has_usable_secret(candidate)
            ),
            "",
        )
    if not api_key and auth_mode != "none":
        source = api_key_env or "a configured API key"
        raise AuthError(
            f"Provider '{requested_provider}' requires {source}, but no credential is configured."
        )

    result = {
        # Keep the configured Provider identity in the runtime.  The endpoint
        # is custom, but capability declarations and route diagnostics are
        # keyed by the user's named Provider (for example, ``deepseek-v``).
        "provider": _normalize_custom_provider_name(requested_provider),
        "base_url": base_url,
        "api_key": api_key or "no-key-required",
        "source": f"custom_provider:{custom_provider.get('name', requested_provider)}",
    }
    # Propagate the model name so callers can override self.model when the
    # provider name differs from the actual model string the API expects.
    if custom_provider.get("model"):
        result["model"] = custom_provider["model"]
    return result


def _resolve_openrouter_runtime(
    *,
    requested_provider: str,
    explicit_api_key: Optional[str] = None,
    explicit_base_url: Optional[str] = None,
) -> Dict[str, Any]:
    model_cfg = _get_model_config()
    cfg_base_url = model_cfg.get("base_url") if isinstance(model_cfg.get("base_url"), str) else ""
    cfg_provider = model_cfg.get("provider") if isinstance(model_cfg.get("provider"), str) else ""
    cfg_api_key = ""
    for k in ("api_key", "api"):
        v = model_cfg.get(k)
        if isinstance(v, str) and v.strip():
            cfg_api_key = v.strip()
            break
    requested_norm = (requested_provider or "").strip().lower()
    cfg_provider = cfg_provider.strip().lower()

    env_openrouter_base_url = env_str("OPENROUTER_BASE_URL")

    # Use the saved provider base_url when available and the provider context
    # matches. OPENAI_BASE_URL env var is no longer consulted — persisted
    # provider config is the single source of truth for endpoint URLs.
    use_config_base_url = False
    if cfg_base_url.strip() and not explicit_base_url:
        if requested_norm == "auto":
            if not cfg_provider or cfg_provider == "auto":
                use_config_base_url = True
        elif requested_norm == "custom" and cfg_provider == "custom":
            use_config_base_url = True

    base_url = (
        (explicit_base_url or "").strip()
        or (cfg_base_url.strip() if use_config_base_url else "")
        or env_openrouter_base_url
        or OPENROUTER_BASE_URL
    ).rstrip("/")

    # Choose API key based on whether the resolved base_url targets OpenRouter.
    # When hitting OpenRouter, prefer OPENROUTER_API_KEY (issue #289).
    # When hitting a custom endpoint (e.g. Z.ai, local LLM), prefer
    # OPENAI_API_KEY so the OpenRouter key doesn't leak to an unrelated
    # provider (issues #420, #560).
    _is_openrouter_url = "openrouter.ai" in base_url
    if _is_openrouter_url:
        api_key_candidates = [
            explicit_api_key,
            env_str("OPENROUTER_API_KEY"),
            env_str("OPENAI_API_KEY"),
        ]
    else:
        # Custom endpoint credentials must come from its configured Provider
        # entry instead of an unrelated global Provider environment variable.
        api_key_candidates = [
            explicit_api_key,
            (cfg_api_key if use_config_base_url else ""),
            env_str("OPENAI_API_KEY"),
            env_str("OPENROUTER_API_KEY"),
        ]
    api_key = next(
        (str(candidate or "").strip() for candidate in api_key_candidates if has_usable_secret(candidate)),
        "",
    )

    source = "explicit" if (explicit_api_key or explicit_base_url) else "env/config"

    # When "custom" was explicitly requested, preserve that as the provider
    # name instead of silently relabeling to "openrouter" (#2562).
    # Also provide a placeholder API key for local servers that don't require
    # authentication — the OpenAI SDK requires a non-empty api_key string.
    effective_provider = "custom" if requested_norm == "custom" else "openrouter"

    # For custom endpoints, check if a credential pool exists
    if effective_provider == "custom" and base_url:
        pool_result = _try_resolve_from_custom_pool(base_url, effective_provider)
        if pool_result:
            return pool_result

    if effective_provider == "custom" and not api_key and not _is_openrouter_url:
        api_key = "no-key-required"

    return {
        "provider": effective_provider,
        "base_url": (
            normalize_openai_compatible_base_url(base_url)
            if effective_provider == "custom"
            else base_url
        ),
        "api_key": api_key,
        "source": source,
    }


def _resolve_explicit_runtime(
    *,
    provider: str,
    requested_provider: str,
    model_cfg: Dict[str, Any],
    explicit_api_key: Optional[str] = None,
    explicit_base_url: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    explicit_api_key = str(explicit_api_key or "").strip()
    explicit_base_url = str(explicit_base_url or "").strip().rstrip("/")
    if not explicit_api_key and not explicit_base_url:
        return None

    if provider == "nous":
        state = auth_mod.get_provider_auth_state("nous") or {}
        base_url = (
            explicit_base_url
            or str(state.get("inference_base_url") or auth_mod.DEFAULT_NOUS_INFERENCE_URL).strip().rstrip("/")
        )
        # Only use agent_key for inference — access_token is an OAuth token for the
        # portal API (minting keys, refreshing tokens), not for the inference API.
        # Falling back to access_token sends an OAuth bearer token to the inference
        # endpoint, which returns 404 because it is not a valid inference credential.
        api_key = explicit_api_key or str(state.get("agent_key") or "").strip()
        expires_at = state.get("agent_key_expires_at") or state.get("expires_at")
        if not api_key:
            creds = resolve_nous_runtime_credentials(
                min_key_ttl_seconds=max(60, int(os.getenv("VOIDCUBE_NOUS_MIN_KEY_TTL_SECONDS", "1800"))),
                timeout_seconds=float(os.getenv("VOIDCUBE_NOUS_TIMEOUT_SECONDS", "15")),
            )
            api_key = creds.get("api_key", "")
            expires_at = creds.get("expires_at")
            if not explicit_base_url:
                base_url = creds.get("base_url", "").rstrip("/") or base_url
        return {
            "provider": "nous",
            "base_url": base_url,
            "api_key": api_key,
            "source": "explicit",
            "expires_at": expires_at,
            "requested_provider": requested_provider,
        }

    pconfig = PROVIDER_REGISTRY.get(provider)
    if pconfig and pconfig.auth_type == "api_key":
        env_url = ""
        if pconfig.base_url_env_var:
            env_url = os.getenv(pconfig.base_url_env_var, "").strip().rstrip("/")

        base_url = explicit_base_url
        if not base_url:
            if provider == "kimi-coding":
                creds = resolve_api_key_provider_credentials(provider)
                base_url = creds.get("base_url", "").rstrip("/")
            else:
                base_url = env_url or pconfig.inference_base_url

        api_key = explicit_api_key
        if not api_key:
            creds = resolve_api_key_provider_credentials(provider)
            api_key = creds.get("api_key", "")
            if not base_url:
                base_url = creds.get("base_url", "").rstrip("/")

        return {
            "provider": provider,
            "base_url": base_url.rstrip("/"),
            "api_key": api_key,
            "source": "explicit",
            "requested_provider": requested_provider,
        }

    return None


def _resolve_local_runtime(
    *,
    provider: str,
    requested_provider: str,
    configured_base_url: str = "",
    explicit_base_url: str = "",
) -> Dict[str, Any]:
    """Resolve one explicitly supported local OpenAI-compatible Provider."""
    provider_config = PROVIDER_REGISTRY[provider]
    env_base_url = ""
    if provider_config.base_url_env_var:
        env_base_url = os.getenv(provider_config.base_url_env_var, "").strip()
    base_url = normalize_openai_compatible_base_url(
        explicit_base_url
        or configured_base_url
        or env_base_url
        or provider_config.inference_base_url
    )
    if explicit_base_url:
        source = "explicit"
    elif configured_base_url:
        source = "provider_config"
    elif env_base_url:
        source = "env"
    else:
        source = "local"
    return {
        "provider": provider,
        "base_url": base_url,
        "api_key": "no-key-required",
        "source": source,
        "requested_provider": requested_provider,
    }


def resolve_runtime_provider(
    *,
    requested: Optional[str] = None,
    explicit_api_key: Optional[str] = None,
    explicit_base_url: Optional[str] = None,
) -> Dict[str, Any]:
    """Resolve runtime provider credentials for agent execution."""
    requested_provider = resolve_requested_provider(requested)
    if not requested_provider:
        raise AuthError("No provider configured. Run /api to configure a provider first.")

    configured_base_url, configured_api_key = _configured_provider_overrides(
        requested_provider
    )
    effective_base_url = (explicit_base_url or "").strip() or configured_base_url
    effective_api_key = (explicit_api_key or "").strip() or configured_api_key

    custom_runtime = _resolve_named_custom_runtime(
        requested_provider=requested_provider,
        explicit_api_key=effective_api_key,
        explicit_base_url=effective_base_url,
    )
    if custom_runtime:
        custom_runtime["requested_provider"] = requested_provider
        return custom_runtime

    provider = resolve_provider(
        requested_provider,
        explicit_api_key=effective_api_key,
        explicit_base_url=effective_base_url,
    )
    if provider != "auto" and provider not in RUNTIME_PROVIDER_IDS:
        raise AuthError(
            f"Provider '{requested_provider}' is not supported by the active runtime. "
            "Configure it as a custom OpenAI-compatible endpoint or choose a listed provider."
        )
    if provider in LOCAL_RUNTIME_PROVIDER_IDS:
        return _resolve_local_runtime(
            provider=provider,
            requested_provider=requested_provider,
            configured_base_url=configured_base_url,
            explicit_base_url=(explicit_base_url or "").strip(),
        )
    model_cfg = _get_model_config()
    explicit_runtime = _resolve_explicit_runtime(
        provider=provider,
        requested_provider=requested_provider,
        model_cfg=model_cfg,
        explicit_api_key=effective_api_key,
        explicit_base_url=effective_base_url,
    )
    if explicit_runtime:
        return explicit_runtime

    should_use_pool = provider in PROVIDER_REGISTRY and provider != "openrouter"
    if provider == "openrouter":
        cfg_provider = str(model_cfg.get("provider") or "").strip().lower()
        cfg_base_url = str(model_cfg.get("base_url") or "").strip()
        env_openai_base_url = env_str("OPENAI_BASE_URL")
        env_openrouter_base_url = env_str("OPENROUTER_BASE_URL")
        has_custom_endpoint = bool(
            explicit_base_url
            or env_openai_base_url
            or env_openrouter_base_url
        )
        if cfg_base_url and cfg_provider in {"auto", "custom"}:
            has_custom_endpoint = True
        has_runtime_override = bool(explicit_api_key or explicit_base_url)
        should_use_pool = (
            requested_provider in {"openrouter", "auto"}
            and not has_custom_endpoint
            and not has_runtime_override
        )

    try:
        pool = load_pool(provider) if should_use_pool else None
    except Exception:
        pool = None
    if pool and pool.has_credentials():
        entry = pool.select()
        pool_api_key = ""
        if entry is not None:
            pool_api_key = (
                getattr(entry, "runtime_api_key", None)
                or getattr(entry, "access_token", "")
            )
        # For Nous, the pool entry's runtime_api_key is the agent_key — a
        # short-lived inference credential (~30 min TTL).  The pool doesn't
        # refresh it during selection (that would trigger network calls in
        # non-runtime contexts like `VoidCube auth list`).  If the key is
        # expired, clear pool_api_key so we fall through to
        # resolve_nous_runtime_credentials() which handles refresh + mint.
        if provider == "nous" and entry is not None and pool_api_key:
            min_ttl = max(60, env_int("VOIDCUBE_NOUS_MIN_KEY_TTL_SECONDS", 1800))
            nous_state = {
                "agent_key": getattr(entry, "agent_key", None),
                "agent_key_expires_at": getattr(entry, "agent_key_expires_at", None),
            }
            if not _agent_key_is_usable(nous_state, min_ttl):
                logger.debug("Nous pool entry agent_key expired/missing, falling through to runtime resolution")
                pool_api_key = ""
        if entry is not None and pool_api_key:
            return _resolve_runtime_from_pool_entry(
                provider=provider,
                entry=entry,
                requested_provider=requested_provider,
                model_cfg=model_cfg,
                pool=pool,
            )

    if provider == "nous":
        try:
            creds = resolve_nous_runtime_credentials(
                min_key_ttl_seconds=max(60, env_int("VOIDCUBE_NOUS_MIN_KEY_TTL_SECONDS", 1800)),
                timeout_seconds=float(env_str("VOIDCUBE_NOUS_TIMEOUT_SECONDS", "15")),
            )
            return {
                "provider": "nous",
                "base_url": creds.get("base_url", "").rstrip("/"),
                "api_key": creds.get("api_key", ""),
                "source": creds.get("source", "portal"),
                "expires_at": creds.get("expires_at"),
                "requested_provider": requested_provider,
            }
        except AuthError:
            if requested_provider != "auto":
                raise
            # Auto-detected Nous but credentials are stale/revoked —
            # fall through to env-var providers (e.g. OpenRouter).
            logger.info("Auto-detected Nous provider but credentials failed; "
                        "falling through to next provider.")

    if provider == "qwen-oauth":
        try:
            creds = resolve_qwen_runtime_credentials()
            return {
                "provider": "qwen-oauth",
                "base_url": creds.get("base_url", "").rstrip("/"),
                "api_key": creds.get("api_key", ""),
                "source": creds.get("source", "qwen-cli"),
                "expires_at_ms": creds.get("expires_at_ms"),
                "requested_provider": requested_provider,
            }
        except AuthError:
            if requested_provider != "auto":
                raise
            logger.info("Qwen OAuth credentials failed; "
                        "falling through to next provider.")

    if provider == "copilot-acp":
        creds = resolve_external_process_provider_credentials(provider)
        return {
            "provider": "copilot-acp",
            "base_url": creds.get("base_url", "").rstrip("/"),
            "api_key": creds.get("api_key", ""),
            "command": creds.get("command", ""),
            "args": list(creds.get("args") or []),
            "source": creds.get("source", "process"),
            "requested_provider": requested_provider,
        }

    # API-key providers (z.ai/GLM, Kimi, MiniMax, MiniMax-CN)
    pconfig = PROVIDER_REGISTRY.get(provider)
    if pconfig and pconfig.auth_type == "api_key":
        creds = resolve_api_key_provider_credentials(provider)
        # Honour the active provider's saved base_url when it matches this provider.
        cfg_provider = str(model_cfg.get("provider") or "").strip().lower()
        cfg_base_url = ""
        if cfg_provider == provider:
            cfg_base_url = (model_cfg.get("base_url") or "").strip().rstrip("/")
        base_url = cfg_base_url or creds.get("base_url", "").rstrip("/")
        return {
            "provider": provider,
            "base_url": base_url,
            "api_key": creds.get("api_key", ""),
            "source": creds.get("source", "env"),
            "requested_provider": requested_provider,
        }

    runtime = _resolve_openrouter_runtime(
        requested_provider=requested_provider,
        explicit_api_key=explicit_api_key,
        explicit_base_url=explicit_base_url,
    )
    runtime["requested_provider"] = requested_provider
    return runtime


def format_runtime_provider_error(error: Exception) -> str:
    if isinstance(error, AuthError):
        return format_auth_error(error)
    return str(error)

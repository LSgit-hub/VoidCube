
"""Shared auxiliary client router for side tasks.

Provides a single resolution chain so every consumer (context compression,
session search, web extraction, vision analysis, browser vision) picks up
the best available backend without duplicating fallback logic.

Resolution order for text tasks (auto mode):
  1. OpenRouter  (OPENROUTER_API_KEY)
  2. Nous Portal (~/.VoidCube/auth.json active provider)
  3. Custom endpoint (saved active provider base_url + OPENAI_API_KEY)
  4. Direct OpenAI-compatible API-key providers
  5. None

Resolution order for vision/multimodal tasks (auto mode):
  1. Selected main provider, if it is one of the supported vision backends below
  2. OpenRouter
  3. Nous Portal
  4. Custom endpoint (for local vision models: Qwen-VL, LLaVA, Pixtral, etc.)
  5. None

Per-task overrides are configured in config.yaml under the ``auxiliary:`` section
(e.g. ``auxiliary.vision.provider``, ``auxiliary.compression.model``).
Default "auto" follows the chains above.

Provider fallback:
  When an automatically resolved provider exhausts credit or cannot be
  reached, the call retries with the next available provider in the shared
  detection chain. Explicit provider choices remain hard constraints.
"""

import json
import logging
import os
import threading
import time
from dataclasses import dataclass
from pathlib import Path  # noqa: F401 — used by test mocks
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

from openai import OpenAI

from agent.credential_pool import load_pool
from agent.api_request import ChatRequestConfig, build_chat_completion_kwargs
from agent.api_response import visible_or_reasoning_text
from agent.integration_policy import require_active_integration
from VoidCube_core.constants import OPENROUTER_BASE_URL, get_VoidCube_home

logger = logging.getLogger(__name__)

# Module-level flag: only warn once per process about stale OPENAI_BASE_URL.
_stale_base_url_warned = False

_PROVIDER_ALIASES = {
    "glm": "zai",
    "z-ai": "zai",
    "z.ai": "zai",
    "zhipu": "zai",
    "kimi": "kimi-coding",
    "moonshot": "kimi-coding",
    "minimax-china": "minimax-cn",
    "minimax_cn": "minimax-cn",
}


def _normalize_aux_provider(provider: Optional[str], *, for_vision: bool = False) -> str:
    normalized = (provider or "auto").strip().lower()
    if normalized.startswith("custom:"):
        suffix = normalized.split(":", 1)[1].strip()
        if not suffix:
            return "custom"
        normalized = suffix if not for_vision else "custom"
    if normalized == "main":
        # Resolve to the user's actual main provider so named custom providers
        # and direct providers such as DeepSeek work correctly.
        main_prov = _read_main_provider()
        if main_prov and main_prov not in ("auto", "main", ""):
            return main_prov
        return "custom"
    return _PROVIDER_ALIASES.get(normalized, normalized)

# OpenRouter app attribution headers
_OR_HEADERS = {
    "HTTP-Referer": "https://VoidCube-agent.nousresearch.com",
    "X-OpenRouter-Title": "Voidcube Agent",
    "X-OpenRouter-Categories": "productivity,cli-agent",
}

_NOUS_DEFAULT_BASE_URL = "https://inference-api.nousresearch.com/v1"
_AUTH_JSON_PATH = get_VoidCube_home() / "auth.json"


def _to_openai_base_url(base_url: str) -> str:
    """Normalize an OpenAI-compatible base URL."""
    return str(base_url or "").strip().rstrip("/")


def _first_live_model(api_key: str, base_url: str) -> Optional[str]:
    """Return the first model currently reported by an OpenAI-compatible API."""
    try:
        from VoidCube_app.models import fetch_api_models

        models = fetch_api_models(api_key, base_url)
    except Exception:
        return None
    return models[0] if models else None


def _select_pool_entry(provider: str) -> Tuple[bool, Optional[Any]]:
    """Return (pool_exists_for_provider, selected_entry)."""
    try:
        pool = load_pool(provider)
    except Exception as exc:
        logger.debug("Auxiliary client: could not load pool for %s: %s", provider, exc)
        return False, None
    if not pool or not pool.has_credentials():
        return False, None
    try:
        return True, pool.select()
    except Exception as exc:
        logger.debug("Auxiliary client: could not select pool entry for %s: %s", provider, exc)
        return True, None


def _pool_runtime_api_key(entry: Any) -> str:
    if entry is None:
        return ""
    # Use the PooledCredential.runtime_api_key property which handles
    # provider-specific fallback (e.g. agent_key for nous).
    key = getattr(entry, "runtime_api_key", None) or getattr(entry, "access_token", "")
    return str(key or "").strip()


def _pool_runtime_base_url(entry: Any, fallback: str = "") -> str:
    if entry is None:
        return str(fallback or "").strip().rstrip("/")
    # runtime_base_url handles provider-specific logic (e.g. nous prefers inference_base_url).
    # Fall back through inference_base_url and base_url for non-PooledCredential entries.
    url = (
        getattr(entry, "runtime_base_url", None)
        or getattr(entry, "inference_base_url", None)
        or getattr(entry, "base_url", None)
        or fallback
    )
    return str(url or "").strip().rstrip("/")


def _read_nous_auth() -> Optional[dict]:
    """Read and validate ~/.VoidCube/auth.json for an active Nous provider.

    Returns the provider state dict if Nous is active with tokens,
    otherwise None.
    """
    pool_present, entry = _select_pool_entry("nous")
    if pool_present:
        if entry is None:
            return None
        return {
            "access_token": getattr(entry, "access_token", ""),
            "refresh_token": getattr(entry, "refresh_token", None),
            "agent_key": getattr(entry, "agent_key", None),
            "inference_base_url": _pool_runtime_base_url(entry, _NOUS_DEFAULT_BASE_URL),
            "portal_base_url": getattr(entry, "portal_base_url", None),
            "client_id": getattr(entry, "client_id", None),
            "scope": getattr(entry, "scope", None),
            "token_type": getattr(entry, "token_type", "Bearer"),
            "source": "pool",
        }

    try:
        if not _AUTH_JSON_PATH.is_file():
            return None
        data = json.loads(_AUTH_JSON_PATH.read_text())
        if data.get("active_provider") != "nous":
            return None
        provider = data.get("providers", {}).get("nous", {})
        # Must have at least an access_token or agent_key
        if not provider.get("agent_key") and not provider.get("access_token"):
            return None
        return provider
    except Exception as exc:
        logger.debug("Could not read Nous auth: %s", exc)
        return None


def _nous_api_key(provider: dict) -> str:
    """Extract the best API key from a Nous provider state dict."""
    return provider.get("agent_key") or provider.get("access_token", "")


def _nous_base_url() -> str:
    """Resolve the Nous inference base URL from env or default."""
    return os.getenv("NOUS_INFERENCE_BASE_URL", _NOUS_DEFAULT_BASE_URL)


def _resolve_api_key_provider() -> Tuple[Optional[OpenAI], Optional[str]]:
    """Try each API-key provider in PROVIDER_REGISTRY order.

    Returns (client, model) for the first provider with usable runtime
    credentials, or (None, None) if none are configured.
    """
    try:
        from VoidCube_app.provider_auth import PROVIDER_REGISTRY, resolve_api_key_provider_credentials
    except ImportError:
        logger.debug("Could not import PROVIDER_REGISTRY for API-key fallback")
        return None, None

    for provider_id, pconfig in PROVIDER_REGISTRY.items():
        if pconfig.auth_type != "api_key":
            continue
        pool_present, entry = _select_pool_entry(provider_id)
        if pool_present:
            api_key = _pool_runtime_api_key(entry)
            if not api_key:
                continue

            base_url = _to_openai_base_url(
                _pool_runtime_base_url(entry, pconfig.inference_base_url) or pconfig.inference_base_url
            )
            model = _first_live_model(api_key, base_url)
            if not model:
                continue
            logger.debug("Auxiliary text client: %s (%s) via pool", pconfig.name, model)
            extra = {}
            if "api.kimi.com" in base_url.lower():
                extra["default_headers"] = {"User-Agent": "KimiCLI/1.30.0"}
            return OpenAI(api_key=api_key, base_url=base_url, **extra), model

        creds = resolve_api_key_provider_credentials(provider_id)
        api_key = str(creds.get("api_key", "")).strip()
        if not api_key:
            continue

        base_url = _to_openai_base_url(
            str(creds.get("base_url", "")).strip().rstrip("/") or pconfig.inference_base_url
        )
        model = _first_live_model(api_key, base_url)
        if not model:
            continue
        logger.debug("Auxiliary text client: %s (%s)", pconfig.name, model)
        extra = {}
        if "api.kimi.com" in base_url.lower():
            extra["default_headers"] = {"User-Agent": "KimiCLI/1.30.0"}
        return OpenAI(api_key=api_key, base_url=base_url, **extra), model

    return None, None


# ── Provider resolution helpers ─────────────────────────────────────────────

def _try_openrouter() -> Tuple[Optional[OpenAI], Optional[str]]:
    pool_present, entry = _select_pool_entry("openrouter")
    if pool_present:
        or_key = _pool_runtime_api_key(entry)
        if not or_key:
            return None, None
        base_url = _pool_runtime_base_url(entry, OPENROUTER_BASE_URL) or OPENROUTER_BASE_URL
        model = _first_live_model(or_key, base_url)
        if not model:
            return None, None
        logger.debug("Auxiliary client: OpenRouter via pool")
        return OpenAI(api_key=or_key, base_url=base_url,
                       default_headers=_OR_HEADERS), model

    or_key = os.getenv("OPENROUTER_API_KEY")
    if not or_key:
        return None, None
    model = _first_live_model(or_key, OPENROUTER_BASE_URL)
    if not model:
        return None, None
    logger.debug("Auxiliary client: OpenRouter")
    return OpenAI(api_key=or_key, base_url=OPENROUTER_BASE_URL,
                   default_headers=_OR_HEADERS), model


def _try_nous(vision: bool = False) -> Tuple[Optional[OpenAI], Optional[str]]:
    nous = _read_nous_auth()
    if not nous:
        return None, None
    base_url = str(nous.get("inference_base_url") or _nous_base_url()).rstrip("/")
    model = _first_live_model(_nous_api_key(nous), base_url)
    if not model:
        return None, None
    logger.debug("Auxiliary client: Nous Portal")
    return (
        OpenAI(
            api_key=_nous_api_key(nous),
            base_url=base_url,
        ),
        model,
    )


def _read_main_model() -> str:
    """Read the user's configured main model from config.yaml.

    The active provider entry is the source of truth for the current model.
    Environment variables are no longer consulted.
    """
    try:
        from VoidCube_app.config import get_active_model_config, load_config
        cfg = load_config()
        model_cfg = get_active_model_config(cfg)
        default = model_cfg.get("default") or model_cfg.get("model") or ""
        if isinstance(default, str) and default.strip():
            return default.strip()
    except Exception:
        pass
    return ""


def _read_main_provider() -> str:
    """Read the user's configured main provider from config.yaml.

    Returns the lowercase provider id (e.g. "alibaba", "openrouter") or ""
    if not configured.
    """
    try:
        from VoidCube_app.config import get_active_provider_key, load_config
        cfg = load_config()
        provider = get_active_provider_key(cfg)
        if isinstance(provider, str) and provider.strip():
            return provider.strip().lower()
    except Exception:
        pass
    return ""


def _resolve_custom_runtime() -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """Resolve the active custom/main endpoint the same way the main CLI does.

    This covers both env-driven OPENAI_BASE_URL setups and config-saved custom
    endpoints where the base URL lives in config.yaml instead of the live
    environment.
    """
    try:
        from VoidCube_app.runtime_provider import resolve_runtime_provider

        runtime = resolve_runtime_provider(requested="custom")
    except Exception as exc:
        logger.debug("Auxiliary client: custom runtime resolution failed: %s", exc)
        runtime = None

    if not isinstance(runtime, dict):
        openai_base = os.getenv("OPENAI_BASE_URL", "").strip().rstrip("/")
        openai_key = os.getenv("OPENAI_API_KEY", "").strip()
        if not openai_base:
            return None, None, None
        runtime = {
            "base_url": openai_base,
            "api_key": openai_key,
        }

    custom_base = runtime.get("base_url")
    custom_key = runtime.get("api_key")
    if not isinstance(custom_base, str) or not custom_base.strip():
        return None, None, None

    custom_base = custom_base.strip().rstrip("/")
    if "openrouter.ai" in custom_base.lower():
        # requested='custom' falls back to OpenRouter when no custom endpoint is
        # configured. Treat that as "no custom endpoint" for auxiliary routing.
        return None, None, None

    # Local servers (Ollama, llama.cpp, vLLM, LM Studio) don't require auth.
    # Use a placeholder key — the OpenAI SDK requires a non-empty string but
    # local servers ignore the Authorization header.  Same fix as cli.py
    # _ensure_runtime_credentials() (PR #2556).
    if not isinstance(custom_key, str) or not custom_key.strip():
        custom_key = "no-key-required"

    return custom_base, custom_key.strip(), None


def _current_custom_base_url() -> str:
    custom_base, _, _ = _resolve_custom_runtime()
    return custom_base or ""


def _try_custom_endpoint() -> Tuple[Optional[OpenAI], Optional[str]]:
    runtime = _resolve_custom_runtime()
    if len(runtime) == 2:
        custom_base, custom_key = runtime
        custom_mode = None
    else:
        custom_base, custom_key, custom_mode = runtime
    if not custom_base or not custom_key:
        return None, None
    model = _read_main_model() or _first_live_model(custom_key, custom_base)
    if not model:
        return None, None
    logger.debug("Auxiliary client: custom endpoint (%s)", model)
    return OpenAI(api_key=custom_key, base_url=custom_base), model


_AUTO_PROVIDER_LABELS = {
    "_try_openrouter": "openrouter",
    "_try_nous": "nous",
    "_try_custom_endpoint": "local/custom",
    "_resolve_api_key_provider": "api-key",
}

_AGGREGATOR_PROVIDERS = frozenset({"openrouter", "nous"})

_MAIN_RUNTIME_FIELDS = ("provider", "model", "base_url", "api_key")


def _normalize_main_runtime(main_runtime: Optional[Dict[str, Any]]) -> Dict[str, str]:
    """Return a sanitized copy of a live main-runtime override."""
    if not isinstance(main_runtime, dict):
        return {}
    normalized: Dict[str, str] = {}
    for field in _MAIN_RUNTIME_FIELDS:
        value = main_runtime.get(field)
        if isinstance(value, str) and value.strip():
            normalized[field] = value.strip()
    provider = normalized.get("provider")
    if provider:
        normalized["provider"] = provider.lower()
    return normalized


def _get_provider_chain() -> List[tuple]:
    """Return the ordered provider detection chain.

    Built at call time (not module level) so that test patches
    on the ``_try_*`` functions are picked up correctly.
    """
    return [
        ("openrouter", _try_openrouter),
        ("nous", _try_nous),
        ("local/custom", _try_custom_endpoint),
        ("api-key", _resolve_api_key_provider),
    ]


def _is_payment_error(exc: Exception) -> bool:
    """Detect payment/credit/quota exhaustion errors.

    Returns True for HTTP 402 (Payment Required) and for 429/other errors
    whose message indicates billing exhaustion rather than rate limiting.
    """
    status = getattr(exc, "status_code", None)
    if status == 402:
        return True
    err_lower = str(exc).lower()
    # OpenRouter and other providers include "credits" or "afford" in 402 bodies,
    # but sometimes wrap them in 429 or other codes.
    if status in (402, 429, None):
        if any(kw in err_lower for kw in ("credits", "insufficient funds",
                                           "can only afford", "billing",
                                           "payment required")):
            return True
    return False


def _is_connection_error(exc: Exception) -> bool:
    """Detect connection/network errors that warrant provider fallback.

    Returns True for errors indicating the provider endpoint is unreachable
    (DNS failure, connection refused, TLS errors, timeouts).  These are
    distinct from API errors (4xx/5xx) which indicate the provider IS
    reachable but returned an error.
    """
    from openai import APIConnectionError, APITimeoutError

    if isinstance(exc, (APIConnectionError, APITimeoutError)):
        return True
    # urllib3 / httpx / httpcore connection errors
    err_type = type(exc).__name__
    if any(kw in err_type for kw in ("Connection", "Timeout", "DNS", "SSL")):
        return True
    err_lower = str(exc).lower()
    if any(kw in err_lower for kw in (
        "connection refused", "name or service not known",
        "no route to host", "network is unreachable",
        "timed out", "connection reset",
    )):
        return True
    return False


def _fallback_chain_label(provider: str) -> str:
    """Map a resolved provider to its one auto-detection chain entry."""
    normalized = _normalize_aux_provider(provider)
    if normalized in ("openrouter", "nous", "api-key"):
        return normalized
    if normalized in ("custom", "local/custom"):
        return "local/custom"
    try:
        from VoidCube_app.runtime_provider import _get_named_custom_provider

        if _get_named_custom_provider(normalized):
            return "local/custom"
    except ImportError:
        pass
    return "api-key"


def _try_provider_fallback(
    failed_provider: str,
    task: str = None,
    reason: str = "payment error",
) -> Tuple[Optional[Any], Optional[str], str]:
    """Try alternative providers after a payment/credit or connection error.

    Iterates the standard auto-detection chain, skipping the provider that
    failed.

    Returns:
        (client, model, provider_label) or (None, None, "") if no fallback.
    """
    skip_chain_label = _fallback_chain_label(failed_provider)

    tried = []
    for label, try_fn in _get_provider_chain():
        if label == skip_chain_label:
            continue
        client, model = try_fn()
        if client is not None:
            logger.info(
                "Auxiliary %s: %s on %s — falling back to %s (%s)",
                task or "call", reason, failed_provider, label, model or "default",
            )
            return client, model, label
        tried.append(label)

    logger.warning(
        "Auxiliary %s: %s on %s and no fallback available (tried: %s)",
        task or "call", reason, failed_provider, ", ".join(tried),
    )
    return None, None, ""


def _resolve_auto(main_runtime: Optional[Dict[str, Any]] = None) -> Tuple[Optional[OpenAI], Optional[str]]:
    """Full auto-detection chain.

    Priority:
      1. If the user's main provider is NOT an aggregator (OpenRouter / Nous),
         use their main provider + main model directly.  This ensures users on
         DeepSeek, ZAI, etc. get auxiliary tasks handled by the same
         provider they already have credentials for — no OpenRouter key needed.
      2. OpenRouter → Nous → custom → API-key providers.
    """
    global _stale_base_url_warned
    runtime = _normalize_main_runtime(main_runtime)
    runtime_provider = runtime.get("provider", "")
    runtime_model = runtime.get("model", "")
    runtime_base_url = runtime.get("base_url", "")
    runtime_api_key = runtime.get("api_key", "")

    # ── Warn once if OPENAI_BASE_URL is set but config.yaml uses a named
    #    provider (not 'custom').  This catches the common "env poisoning"
    #    scenario where a user switches providers via `VoidCube model` but the
    #    old OPENAI_BASE_URL lingers in ~/.VoidCube/.env. ──
    if not _stale_base_url_warned:
        _env_base = os.getenv("OPENAI_BASE_URL", "").strip()
        _cfg_provider = runtime_provider or _read_main_provider()
        if (_env_base and _cfg_provider
                and _cfg_provider != "custom"
                and not _cfg_provider.startswith("custom:")):
            logger.warning(
                "OPENAI_BASE_URL is set (%s) but the active provider is '%s'. "
                "Auxiliary clients may route to the wrong endpoint. "
                "Run: VoidCube model to reconfigure, or remove "
                "OPENAI_BASE_URL from ~/.VoidCube/.env",
                _env_base, _cfg_provider,
            )
            _stale_base_url_warned = True

    # ── Step 1: non-aggregator main provider → use main model directly ──
    main_provider = runtime_provider or _read_main_provider()
    main_model = runtime_model or _read_main_model()
    if (main_provider and main_model
            and main_provider not in _AGGREGATOR_PROVIDERS
            and main_provider not in ("auto", "")):
        resolved_provider = main_provider
        explicit_base_url = None
        explicit_api_key = None
        if runtime_base_url and (main_provider == "custom" or main_provider.startswith("custom:")):
            resolved_provider = "custom"
            explicit_base_url = runtime_base_url
            explicit_api_key = runtime_api_key or None
        client, resolved = resolve_provider_client(
            resolved_provider,
            main_model,
            explicit_base_url=explicit_base_url,
            explicit_api_key=explicit_api_key,
        )
        if client is not None:
            logger.info("Auxiliary auto-detect: using main provider %s (%s)",
                        main_provider, resolved or main_model)
            return client, resolved or main_model

    # ── Step 2: aggregator / fallback chain ──────────────────────────────
    tried: list = []
    for label, try_fn in _get_provider_chain():
        client, model = try_fn()
        if client is not None:
            if tried:
                logger.info("Auxiliary auto-detect: using %s (%s) — skipped: %s",
                            label, model or "default", ", ".join(tried))
            else:
                logger.info("Auxiliary auto-detect: using %s (%s)", label, model or "default")
            return client, model
        tried.append(label)
    logger.warning("Auxiliary auto-detect: no provider available (tried: %s). "
                   "Compression, summarization, and memory flush will not work. "
                   "Set OPENROUTER_API_KEY or configure a local model in config.yaml.",
                   ", ".join(tried))
    return None, None


# ── Centralized Provider Router ─────────────────────────────────────────────
#
# resolve_provider_client() is the single entry point for creating a properly
# configured client given a (provider, model) pair.  It handles auth lookup,
# base URL resolution and provider-specific headers.
#
# All auxiliary consumer code should go through this or the public helpers
# below — never look up auth env vars ad-hoc.


def _to_async_client(sync_client, model: str):
    """Convert a sync OpenAI-compatible client to its async counterpart."""
    from openai import AsyncOpenAI

    async_kwargs = {
        "api_key": sync_client.api_key,

        "base_url": str(sync_client.base_url),
    }
    base_lower = str(sync_client.base_url).lower()
    if "openrouter" in base_lower:
        async_kwargs["default_headers"] = dict(_OR_HEADERS)
    elif "api.kimi.com" in base_lower:
        async_kwargs["default_headers"] = {"User-Agent": "KimiCLI/1.30.0"}
    return AsyncOpenAI(**async_kwargs), model


def _normalize_resolved_model(model_name: Optional[str], provider: str) -> Optional[str]:
    """Normalize a resolved model for the provider that will receive it."""
    if not model_name:
        return model_name
    try:
        from VoidCube_app.model_normalization import normalize_model_for_provider

        return normalize_model_for_provider(model_name, provider)
    except Exception:
        return model_name


def resolve_provider_client(
    provider: str,
    model: str = None,
    async_mode: bool = False,
    explicit_base_url: str = None,
    explicit_api_key: str = None,
    main_runtime: Optional[Dict[str, Any]] = None,
) -> Tuple[Optional[Any], Optional[str]]:
    """Central router: given a provider name and optional model, return a
    configured client with the correct auth, base URL, and API format.

    The returned client exposes ``.chat.completions.create()``.

    Args:
        provider: Provider identifier such as "openrouter", "nous", an
            OpenAI-compatible API-key provider, "custom", or "auto".
        model: Model slug override. If None, uses the provider default.
        async_mode: If True, return an async-compatible client.
        explicit_base_url: Optional direct OpenAI-compatible endpoint.
        explicit_api_key: Optional API key paired with explicit_base_url.

    Returns:
        (client, resolved_model) or (None, None) if auth is unavailable.
    """
    require_active_integration(provider, model, explicit_base_url)

    # Normalise aliases
    provider = _normalize_aux_provider(provider)

    if provider == "auto":
        client, resolved = _resolve_auto(main_runtime=main_runtime)
        if client is None:
            return None, None
        # A vendor-prefixed override may not belong to a local endpoint. Drop
        # it only when the live endpoint returned a bare model ID.
        if model and "/" in model and resolved and "/" not in resolved:
            logger.debug(
                "Dropping OpenRouter-format model %r for non-OpenRouter "
                "auxiliary provider (using %r instead)", model, resolved)
            model = None
        final_model = model or resolved
        return (_to_async_client(client, final_model) if async_mode
                else (client, final_model))

    # ── OpenRouter ───────────────────────────────────────────────────
    if provider == "openrouter":
        client, default = _try_openrouter()
        if client is None:
            logger.warning("resolve_provider_client: openrouter requested "
                           "but OPENROUTER_API_KEY not set")
            return None, None
        final_model = _normalize_resolved_model(model or default, provider)
        return (_to_async_client(client, final_model) if async_mode
                else (client, final_model))

    # ── Nous Portal (OAuth) ──────────────────────────────────────────
    if provider == "nous":
        client, default = _try_nous()
        if client is None:
            logger.warning("resolve_provider_client: nous requested "
                           "but Nous Portal not configured (run: VoidCube auth)")
            return None, None
        final_model = _normalize_resolved_model(model or default, provider)
        return (_to_async_client(client, final_model) if async_mode
                else (client, final_model))

    if provider == "custom":
        if explicit_base_url:
            custom_base = explicit_base_url.strip()
            custom_key = (
                (explicit_api_key or "").strip()
                or os.getenv("OPENAI_API_KEY", "").strip()
                or "no-key-required"  # local servers don't need auth
            )
            if not custom_base:
                logger.warning(
                    "resolve_provider_client: explicit custom endpoint requested "
                    "but base_url is empty"
                )
                return None, None
            final_model = _normalize_resolved_model(
                model
                or _read_main_model()
                or _first_live_model(custom_key, custom_base),
                provider,
            )
            if not final_model:
                logger.warning(
                    "resolve_provider_client: custom endpoint did not return any models"
                )
                return None, None
            extra = {}
            if "api.kimi.com" in custom_base.lower():
                extra["default_headers"] = {"User-Agent": "KimiCLI/1.30.0"}
            client = OpenAI(api_key=custom_key, base_url=custom_base, **extra)
            return (_to_async_client(client, final_model) if async_mode
                    else (client, final_model))
        for try_fn in (_try_custom_endpoint, _resolve_api_key_provider):
            client, default = try_fn()
            if client is not None:
                final_model = _normalize_resolved_model(model or default, provider)
                return (_to_async_client(client, final_model) if async_mode
                        else (client, final_model))
        logger.warning("resolve_provider_client: custom/main requested "
                       "but no endpoint credentials found")
        return None, None

    # ── Named custom providers (config.yaml providers map) ───
    try:
        from VoidCube_app.runtime_provider import _get_named_custom_provider
        custom_entry = _get_named_custom_provider(provider)
        if custom_entry:
            custom_base = custom_entry.get("base_url", "").strip()
            custom_key = custom_entry.get("api_key", "").strip() or "no-key-required"
            if custom_base:
                final_model = _normalize_resolved_model(
                    model
                    or _read_main_model()
                    or _first_live_model(custom_key, custom_base),
                    provider,
                )
                if not final_model:
                    logger.warning(
                        "resolve_provider_client: named custom provider %r did not return any models",
                        provider,
                    )
                    return None, None
                client = OpenAI(api_key=custom_key, base_url=custom_base)
                logger.debug(
                    "resolve_provider_client: named custom provider %r (%s)",
                    provider, final_model)
                return (_to_async_client(client, final_model) if async_mode
                        else (client, final_model))
            logger.warning(
                "resolve_provider_client: named custom provider %r has no base_url",
                provider)
            return None, None
    except ImportError:
        pass

    # ── API-key providers from PROVIDER_REGISTRY ─────────────────────
    try:
        from VoidCube_app.provider_auth import PROVIDER_REGISTRY, resolve_api_key_provider_credentials
    except ImportError:
        logger.debug("VoidCube_app.provider_auth not available for provider %s", provider)
        return None, None

    pconfig = PROVIDER_REGISTRY.get(provider)
    if pconfig is None:
        logger.warning("resolve_provider_client: unknown provider %r", provider)
        return None, None

    if pconfig.auth_type == "api_key":
        creds = resolve_api_key_provider_credentials(provider)
        api_key = str(creds.get("api_key", "")).strip()
        if not api_key:
            tried_sources = list(pconfig.api_key_env_vars)
            logger.debug("resolve_provider_client: provider %s has no API "
                         "key configured (tried: %s)",
                         provider, ", ".join(tried_sources))
            return None, None

        base_url = _to_openai_base_url(
            str(creds.get("base_url", "")).strip().rstrip("/") or pconfig.inference_base_url
        )

        default_model = _first_live_model(api_key, base_url)
        if not model and not default_model:
            logger.warning(
                "resolve_provider_client: provider %s did not return any models",
                provider,
            )
            return None, None
        final_model = _normalize_resolved_model(model or default_model, provider)

        # Provider-specific headers
        headers = {}
        if "api.kimi.com" in base_url.lower():
            headers["User-Agent"] = "KimiCLI/1.30.0"
        client = OpenAI(api_key=api_key, base_url=base_url,
                        **({"default_headers": headers} if headers else {}))

        logger.debug("resolve_provider_client: %s (%s)", provider, final_model)
        return (_to_async_client(client, final_model) if async_mode
                else (client, final_model))

    elif pconfig.auth_type in ("oauth_device_code", "oauth_external"):
        # OAuth providers — route through their specific try functions
        if provider == "nous":
            return resolve_provider_client("nous", model, async_mode)
        # Other OAuth providers not directly supported
        logger.warning("resolve_provider_client: OAuth provider %s not "
                       "directly supported, try 'auto'", provider)
        return None, None

    logger.warning("resolve_provider_client: unhandled auth_type %s for %s",
                   pconfig.auth_type, provider)
    return None, None


# ── Public API ──────────────────────────────────────────────────────────────

def get_text_auxiliary_client(
    task: str = "",
    *,
    main_runtime: Optional[Dict[str, Any]] = None,
) -> Tuple[Optional[OpenAI], Optional[str]]:
    """Return (client, default_model_slug) for text-only auxiliary tasks.

    Args:
        task: Optional task name ("compression", "web_extract") to check
              for a task-specific provider override.

    Per-task model overrides come from ``auxiliary.<task>.model``.
    """
    provider, model, base_url, api_key = _resolve_task_provider_model(task or None)
    return resolve_provider_client(
        provider,
        model=model,
        explicit_base_url=base_url,
        explicit_api_key=api_key,
        main_runtime=main_runtime,
    )


def get_async_text_auxiliary_client(task: str = "", *, main_runtime: Optional[Dict[str, Any]] = None):
    """Return (async_client, model_slug) for async consumers.

    Returns an AsyncOpenAI-compatible client and model.
    Returns (None, None) when no provider is available.
    """
    provider, model, base_url, api_key = _resolve_task_provider_model(task or None)
    return resolve_provider_client(
        provider,
        model=model,
        async_mode=True,
        explicit_base_url=base_url,
        explicit_api_key=api_key,
        main_runtime=main_runtime,
    )


_VISION_AUTO_PROVIDER_ORDER = (
    "openrouter",
    "nous",
)


def _normalize_vision_provider(provider: Optional[str]) -> str:
    return _normalize_aux_provider(provider, for_vision=True)


def _resolve_strict_vision_backend(provider: str) -> Tuple[Optional[Any], Optional[str]]:
    provider = _normalize_vision_provider(provider)
    if provider == "openrouter":
        return _try_openrouter()
    if provider == "nous":
        return _try_nous(vision=True)
    if provider == "custom":
        return _try_custom_endpoint()
    return None, None


def _strict_vision_backend_available(provider: str) -> bool:
    return _resolve_strict_vision_backend(provider)[0] is not None


def _vision_provider_configured(provider: str) -> bool:
    """Check vision credentials/endpoints without making a network request.

    This is intentionally separate from ``_strict_vision_backend_available``:
    the latter validates a backend by querying its model endpoint and is only
    appropriate immediately before a vision request, never during CLI startup.
    """
    provider = _normalize_vision_provider(provider)
    if provider == "openrouter":
        pool_present, entry = _select_pool_entry("openrouter")
        return bool(_pool_runtime_api_key(entry)) if pool_present else bool(
            os.getenv("OPENROUTER_API_KEY", "").strip()
        )
    if provider == "nous":
        return bool(_read_nous_auth())
    if provider == "custom":
        return bool(_resolve_custom_runtime()[0])

    try:
        from VoidCube_app.provider_auth import PROVIDER_REGISTRY, resolve_api_key_provider_credentials
        pconfig = PROVIDER_REGISTRY.get(provider)
        if pconfig is None:
            return False
        if pconfig.auth_type != "api_key":
            return bool(pconfig.inference_base_url)
        pool_present, entry = _select_pool_entry(provider)
        if pool_present:
            return bool(_pool_runtime_api_key(entry))
        return bool(resolve_api_key_provider_credentials(provider).get("api_key", "").strip())
    except Exception:
        return False


def get_configured_vision_backends() -> List[str]:
    """Return locally configured vision backends without probing the network."""
    configured: List[str] = []
    try:
        requested, _model, base_url, _api_key = _resolve_task_provider_model("vision")
    except Exception:
        requested, base_url = "auto", None

    # A task-specific endpoint is itself sufficient evidence for startup
    # gating; validating it belongs to the first actual vision request.
    if base_url:
        configured.append("custom")
    elif requested not in ("auto", "") and _vision_provider_configured(requested):
        configured.append(_normalize_vision_provider(requested))
    elif requested in ("auto", ""):
        main_provider = _read_main_provider()
        if main_provider and main_provider not in ("auto", "") and _vision_provider_configured(main_provider):
            configured.append(_normalize_vision_provider(main_provider))
        for candidate in _VISION_AUTO_PROVIDER_ORDER:
            if candidate not in configured and _vision_provider_configured(candidate):
                configured.append(candidate)
        if _vision_provider_configured("custom") and "custom" not in configured:
            configured.append("custom")
    return configured


def get_available_vision_backends() -> List[str]:

    """Return the currently available vision backends in auto-selection order.

    Order: active provider → OpenRouter → Nous → stop.  This is the single
    source of truth for setup, tool gating, and runtime auto-routing of
    vision tasks.
    """
    available: List[str] = []
    # 1. Active provider — if the user configured a provider, try it first.
    main_provider = _read_main_provider()
    if main_provider and main_provider not in ("auto", ""):
        if main_provider in _VISION_AUTO_PROVIDER_ORDER:
            if _strict_vision_backend_available(main_provider):
                available.append(main_provider)
        else:
            client, _ = resolve_provider_client(main_provider, _read_main_model())
            if client is not None:
                available.append(main_provider)
    # 2. OpenRouter, 3. Nous — skip if already covered by main provider.
    for p in _VISION_AUTO_PROVIDER_ORDER:
        if p not in available and _strict_vision_backend_available(p):
            available.append(p)
    return available


def resolve_vision_provider_client(
    provider: Optional[str] = None,
    model: Optional[str] = None,
    *,
    base_url: Optional[str] = None,
    api_key: Optional[str] = None,
    async_mode: bool = False,
) -> Tuple[Optional[str], Optional[Any], Optional[str]]:
    """Resolve the client actually used for vision tasks.

    Direct endpoint overrides take precedence over provider selection. Explicit
    provider overrides still use the generic provider router for non-standard
    backends, so users can intentionally force experimental providers. Auto mode
    stays conservative and only tries vision backends known to work today.
    """
    requested, resolved_model, resolved_base_url, resolved_api_key = _resolve_task_provider_model(
        "vision", provider, model, base_url, api_key
    )
    requested = _normalize_vision_provider(requested)

    def _finalize(resolved_provider: str, sync_client: Any, default_model: Optional[str]):
        if sync_client is None:
            return resolved_provider, None, None
        final_model = resolved_model or default_model
        if async_mode:
            async_client, async_model = _to_async_client(sync_client, final_model)
            return resolved_provider, async_client, async_model
        return resolved_provider, sync_client, final_model

    if resolved_base_url:
        client, final_model = resolve_provider_client(
            "custom",
            model=resolved_model,
            async_mode=async_mode,
            explicit_base_url=resolved_base_url,
            explicit_api_key=resolved_api_key,
        )
        if client is None:
            return "custom", None, None
        return "custom", client, final_model

    if requested == "auto":
        # Vision auto-detection order:
        #   1. Active provider + model (user's main chat config)
        #   2. OpenRouter  (known vision-capable default model)
        #   3. Nous Portal (known vision-capable default model)
        #   4. Stop
        main_provider = _read_main_provider()
        main_model = _read_main_model()
        if main_provider and main_provider not in ("auto", ""):
            if main_provider in _VISION_AUTO_PROVIDER_ORDER:
                # Known strict backend — use its defaults.
                sync_client, default_model = _resolve_strict_vision_backend(main_provider)
                if sync_client is not None:
                    return _finalize(main_provider, sync_client, default_model)
            else:
                # Direct and named custom providers use their selected main model.
                vision_model = main_model
                rpc_client, rpc_model = resolve_provider_client(
                    main_provider, vision_model)
                if rpc_client is not None:
                    logger.info(
                        "Vision auto-detect: using active provider %s (%s)",
                        main_provider, rpc_model or vision_model,
                    )
                    return _finalize(
                        main_provider, rpc_client, rpc_model or vision_model)

        # Fall back through aggregators.
        for candidate in _VISION_AUTO_PROVIDER_ORDER:
            if candidate == main_provider:
                continue  # already tried above
            sync_client, default_model = _resolve_strict_vision_backend(candidate)
            if sync_client is not None:
                return _finalize(candidate, sync_client, default_model)

        logger.debug("Auxiliary vision client: none available")
        return None, None, None

    if requested in _VISION_AUTO_PROVIDER_ORDER:
        sync_client, default_model = _resolve_strict_vision_backend(requested)
        return _finalize(requested, sync_client, default_model)

    client, final_model = _get_cached_client(requested, resolved_model, async_mode)
    if client is None:
        return requested, None, None
    return requested, client, final_model


# ── Centralized LLM Call API ────────────────────────────────────────────────
#
# call_llm() and async_call_llm() own the full request lifecycle:
#   1. Resolve provider + model from task config (or explicit args)
#   2. Get or create a cached client for that provider
#   3. Format request args for the provider + model (max_tokens handling, etc.)
#   4. Make the API call
#   5. Return the response
#
# Every auxiliary LLM consumer should use these instead of manually
# constructing clients and calling .chat.completions.create().

# Client cache: (provider, async_mode, base_url, api_key) -> (client, default_model)
_client_cache: Dict[tuple, tuple] = {}
_client_cache_lock = threading.Lock()


def neuter_async_httpx_del() -> None:
    """Monkey-patch ``AsyncHttpxClientWrapper.__del__`` to be a no-op.

    The OpenAI SDK's ``AsyncHttpxClientWrapper.__del__`` schedules
    ``self.aclose()`` via ``asyncio.get_running_loop().create_task()``.
    When an ``AsyncOpenAI`` client is garbage-collected while
    prompt_toolkit's event loop is running (the common CLI idle state),
    the ``aclose()`` task runs on prompt_toolkit's loop but the
    underlying TCP transport is bound to a *different* loop (the worker
    thread's loop that the client was originally created on).  If that
    loop is closed or its thread is dead, the transport's
    ``self._loop.call_soon()`` raises ``RuntimeError("Event loop is
    closed")``, which prompt_toolkit surfaces as "Unhandled exception
    in event loop ... Press ENTER to continue...".

    Neutering ``__del__`` is safe because:
    - Cached clients are explicitly cleaned via ``_force_close_async_httpx``
      on stale-loop detection and ``shutdown_cached_clients`` on exit.
    - Uncached clients' TCP connections are cleaned up by the OS when the
      process exits.
    - The OpenAI SDK itself marks this as a TODO (``# TODO(someday):
      support non asyncio runtimes here``).

    Call this once at CLI startup, before any ``AsyncOpenAI`` clients are
    created.
    """
    try:
        from openai._base_client import AsyncHttpxClientWrapper
        AsyncHttpxClientWrapper.__del__ = lambda self: None  # type: ignore[assignment]
    except (ImportError, AttributeError):
        pass  # Graceful degradation if the SDK changes its internals


def _force_close_async_httpx(client: Any) -> None:
    """Mark the httpx AsyncClient inside an AsyncOpenAI client as closed.

    This prevents ``AsyncHttpxClientWrapper.__del__`` from scheduling
    ``aclose()`` on a (potentially closed) event loop, which causes
    ``RuntimeError: Event loop is closed`` → prompt_toolkit's
    "Press ENTER to continue..." handler.

    We intentionally do NOT run the full async close path — the
    connections will be dropped by the OS when the process exits.
    """
    try:
        from httpx._client import ClientState
        inner = getattr(client, "_client", None)
        if inner is not None and not getattr(inner, "is_closed", True):
            inner._state = ClientState.CLOSED
    except Exception:
        pass


def shutdown_cached_clients() -> None:
    """Close all cached clients (sync and async) to prevent event-loop errors.

    Call this during CLI shutdown, *before* the event loop is closed, to
    avoid ``AsyncHttpxClientWrapper.__del__`` raising on a dead loop.
    """
    import inspect

    with _client_cache_lock:
        for key, entry in list(_client_cache.items()):
            client = entry[0]
            if client is None:
                continue
            # Mark any async httpx transport as closed first (prevents __del__
            # from scheduling aclose() on a dead event loop).
            _force_close_async_httpx(client)
            # Sync clients: close the httpx connection pool cleanly.
            # Async clients: skip — we already neutered __del__ above.
            try:
                close_fn = getattr(client, "close", None)
                if close_fn and not inspect.iscoroutinefunction(close_fn):
                    close_fn()
            except Exception:
                pass
        _client_cache.clear()


def cleanup_stale_async_clients() -> None:
    """Force-close cached async clients whose event loop is closed.

    Call this after each agent turn to proactively clean up stale clients
    before GC can trigger ``AsyncHttpxClientWrapper.__del__`` on them.
    This is defense-in-depth — the primary fix is ``neuter_async_httpx_del``
    which disables ``__del__`` entirely.
    """
    with _client_cache_lock:
        stale_keys = []
        for key, entry in _client_cache.items():
            client, _default, cached_loop = entry
            if cached_loop is not None and cached_loop.is_closed():
                _force_close_async_httpx(client)
                stale_keys.append(key)
        for key in stale_keys:
            del _client_cache[key]


def _is_openrouter_client(client: Any) -> bool:
    for obj in (client, getattr(client, "_client", None), getattr(client, "client", None)):
        if obj and "openrouter" in str(getattr(obj, "base_url", "") or "").lower():
            return True
    return False


def _compat_model(client: Any, model: Optional[str], cached_default: Optional[str]) -> Optional[str]:
    """Drop OpenRouter-format model slugs (with '/') for non-OpenRouter clients.

    Mirrors the guard in resolve_provider_client() which is skipped on cache hits.
    """
    if model and "/" in model and not _is_openrouter_client(client):
        return cached_default
    return model or cached_default


def _get_cached_client(
    provider: str,
    model: str = None,
    async_mode: bool = False,
    base_url: str = None,
    api_key: str = None,
    main_runtime: Optional[Dict[str, Any]] = None,
) -> Tuple[Optional[Any], Optional[str]]:
    """Get or create a cached client for the given provider.

    Async clients (AsyncOpenAI) use httpx.AsyncClient internally, which
    binds to the event loop that was current when the client was created.
    Using such a client on a *different* loop causes deadlocks or
    RuntimeError.  To prevent cross-loop issues (especially in gateway
    mode where _run_async() may spawn fresh loops in worker threads), the
    cache key for async clients includes the current event loop's identity
    so each loop gets its own client instance.
    """
    # Include loop identity for async clients to prevent cross-loop reuse.
    # httpx.AsyncClient (inside AsyncOpenAI) is bound to the loop where it
    # was created — reusing it on a different loop causes deadlocks (#2681).
    loop_id = 0
    current_loop = None
    if async_mode:
        try:
            import asyncio as _aio
            current_loop = _aio.get_event_loop()
            loop_id = id(current_loop)
        except RuntimeError:
            pass
    runtime = _normalize_main_runtime(main_runtime)
    runtime_key = tuple(runtime.get(field, "") for field in _MAIN_RUNTIME_FIELDS) if provider == "auto" else ()
    cache_key = (provider, async_mode, base_url or "", api_key or "", loop_id, runtime_key)
    with _client_cache_lock:
        if cache_key in _client_cache:
            cached_client, cached_default, cached_loop = _client_cache[cache_key]
            if async_mode:
                # A cached async client whose loop has been closed will raise
                # "Event loop is closed" when httpx tries to clean up its
                # transport.  Discard the stale client and create a fresh one.
                if cached_loop is not None and cached_loop.is_closed():
                    _force_close_async_httpx(cached_client)
                    del _client_cache[cache_key]
                else:
                    effective = _compat_model(cached_client, model, cached_default)
                    return cached_client, effective
            else:
                effective = _compat_model(cached_client, model, cached_default)
                return cached_client, effective
    # Build outside the lock
    client, default_model = resolve_provider_client(
        provider,
        model,
        async_mode,
        explicit_base_url=base_url,
        explicit_api_key=api_key,
        main_runtime=runtime,
    )
    if client is not None:
        # For async clients, remember which loop they were created on so we
        # can detect stale entries later.
        bound_loop = current_loop
        with _client_cache_lock:
            if cache_key not in _client_cache:
                _client_cache[cache_key] = (client, default_model, bound_loop)
            else:
                client, default_model, _ = _client_cache[cache_key]
    return client, model or default_model


def _resolve_task_provider_model(
    task: str = None,
    provider: str = None,
    model: str = None,
    base_url: str = None,
    api_key: str = None,
) -> Tuple[str, Optional[str], Optional[str], Optional[str]]:
    """Determine provider + model for a call.

    Priority:
      1. Explicit provider/model/base_url/api_key args (always win)
      2. Config file (auxiliary.{task}.*)
      3. "auto" (full auto-detection chain)

    Returns (provider, model, base_url, api_key). When base_url is set,
    provider is forced to "custom" and the task uses that direct endpoint.
    """
    config = {}
    cfg_provider = None
    cfg_model = None
    cfg_base_url = None
    cfg_api_key = None

    if task:
        try:
            from VoidCube_app.config import load_config
            config = load_config()
        except ImportError:
            config = {}

        aux = config.get("auxiliary", {}) if isinstance(config, dict) else {}
        task_config = aux.get(task, {}) if isinstance(aux, dict) else {}
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
        # Config.yaml is the primary source for per-task overrides.
        if cfg_base_url:
            return "custom", resolved_model, cfg_base_url, cfg_api_key
        if cfg_provider and cfg_provider != "auto":
            return cfg_provider, resolved_model, None, None

        return "auto", resolved_model, None, None

    return "auto", resolved_model, None, None


_DEFAULT_AUX_TIMEOUT = 30.0


def _get_task_timeout(task: str, default: float = _DEFAULT_AUX_TIMEOUT) -> float:
    """Read timeout from auxiliary.{task}.timeout in config, falling back to *default*."""
    if not task:
        return default
    try:
        from VoidCube_app.config import load_config
        config = load_config()
    except ImportError:
        return default
    aux = config.get("auxiliary", {}) if isinstance(config, dict) else {}
    task_config = aux.get(task, {}) if isinstance(aux, dict) else {}
    raw = task_config.get("timeout")
    if raw is not None:
        try:
            return float(raw)
        except (ValueError, TypeError):
            pass
    return default


def _build_call_kwargs(
    provider: str,
    model: str,
    messages: list,
    temperature: Optional[float] = None,
    max_tokens: Optional[int] = None,
    tools: Optional[list] = None,
    timeout: float = 30.0,
    extra_body: Optional[dict] = None,
    base_url: Optional[str] = None,
) -> dict:
    """Build kwargs for .chat.completions.create() with model/provider adjustments."""
    require_active_integration(provider)
    effective_base_url = base_url or (
        _current_custom_base_url() if provider == "custom" else ""
    )
    merged_extra = dict(extra_body or {})
    if provider == "nous":
        tags = merged_extra.setdefault("tags", [])
        if "product=VoidCube-agent" not in tags:
            tags.append("product=VoidCube-agent")

    overrides: Dict[str, Any] = {}
    if temperature is not None:
        overrides["temperature"] = temperature
    if merged_extra:
        overrides["extra_body"] = merged_extra

    return build_chat_completion_kwargs(
        ChatRequestConfig(
            model=model,
            base_url=effective_base_url,
            tools=tuple(tools or ()),
            max_tokens=max_tokens,
            include_reasoning=False,
            request_overrides=overrides,
            timeout=timeout,
        ),
        messages,
    )


@dataclass(frozen=True, slots=True)
class AuxiliaryCallTarget:
    """Resolved client and route for one auxiliary request."""

    requested_provider: str
    active_provider: str
    model: str
    base_url: str
    client: Any


@dataclass(frozen=True, slots=True)
class AuxiliaryFallbackCall:
    """Prepared fallback client and request payload."""

    provider: str
    model: str
    client: Any
    kwargs: Dict[str, Any]


def _infer_active_provider(
    requested_provider: str,
    client: Any,
    main_runtime: Optional[Dict[str, Any]],
) -> str:
    if requested_provider != "auto":
        return requested_provider
    base_url = str(getattr(client, "base_url", "") or "")
    host = (urlparse(base_url).hostname or "").casefold()
    if host == "openrouter.ai" or host.endswith(".openrouter.ai"):
        return "openrouter"
    if host == "nousresearch.com" or host.endswith(".nousresearch.com"):
        return "nous"
    try:
        from VoidCube_app.provider_auth import PROVIDER_REGISTRY

        for provider_id, config in PROVIDER_REGISTRY.items():
            candidate_urls = (
                config.get("base_url", ""),
                config.get("inference_base_url", ""),
            )
            if any(
                host
                and host == (urlparse(str(candidate)).hostname or "").casefold()
                for candidate in candidate_urls
            ):
                return provider_id
    except ImportError:
        pass
    runtime_provider = _normalize_main_runtime(main_runtime).get("provider", "")
    configured_provider = _read_main_provider()
    if runtime_provider and runtime_provider not in ("auto", ""):
        return runtime_provider
    if configured_provider and configured_provider not in ("auto", ""):
        return configured_provider
    return "api-key"


def _missing_provider_error(task: Optional[str], provider: str) -> RuntimeError:
    return RuntimeError(
        f"No LLM provider configured for task={task} provider={provider}. Run: /api"
    )


def _resolve_auxiliary_call_target(
    *,
    task: Optional[str],
    provider: Optional[str],
    model: Optional[str],
    base_url: Optional[str],
    api_key: Optional[str],
    main_runtime: Optional[Dict[str, Any]],
    async_mode: bool,
) -> AuxiliaryCallTarget:
    requested_provider, resolved_model, resolved_base_url, resolved_api_key = (
        _resolve_task_provider_model(task, provider, model, base_url, api_key)
    )
    require_active_integration(
        requested_provider,
        resolved_model,
        resolved_base_url,
    )

    if task == "vision":
        active_provider, client, final_model = resolve_vision_provider_client(
            provider=provider,
            model=model,
            base_url=base_url,
            api_key=api_key,
            async_mode=async_mode,
        )
        if client is None:
            raise _missing_provider_error(task, requested_provider)
        active_provider = active_provider or requested_provider
    else:
        client, final_model = _get_cached_client(
            requested_provider,
            resolved_model,
            async_mode=async_mode,
            base_url=resolved_base_url,
            api_key=resolved_api_key,
            main_runtime=main_runtime,
        )
        if client is None:
            explicit = (requested_provider or "").strip().lower()
            if explicit and explicit not in ("auto", "openrouter", "custom"):
                raise RuntimeError(
                    f"Provider '{explicit}' is set in config.yaml but no API key "
                    f"was found. Set the {explicit.upper()}_API_KEY environment "
                    "variable, or switch to a different provider with `/model`."
                )
            if requested_provider == "auto":
                logger.info(
                    "Auxiliary %s: provider %s unavailable, trying auto-detection chain",
                    task or "call",
                    requested_provider,
                )
                client, final_model = _get_cached_client(
                    "auto",
                    resolved_model,
                    async_mode=async_mode,
                    main_runtime=main_runtime,
                )
        if client is None:
            raise _missing_provider_error(task, requested_provider)
        active_provider = _infer_active_provider(
            requested_provider,
            client,
            main_runtime,
        )

    final_model = str(final_model or resolved_model or "").strip()
    if not final_model:
        raise RuntimeError(
            f"No LLM model resolved for task={task} provider={active_provider}"
        )
    effective_base_url = str(
        getattr(client, "base_url", "") or resolved_base_url or ""
    )
    return AuxiliaryCallTarget(
        requested_provider=requested_provider,
        active_provider=active_provider,
        model=final_model,
        base_url=effective_base_url,
        client=client,
    )


def _completion_token_retry_kwargs(
    kwargs: Dict[str, Any],
    error: Exception,
    max_tokens: Optional[int],
) -> Optional[Dict[str, Any]]:
    """Return a max-completion-token retry payload when the error names max_tokens."""
    if max_tokens is None or "max_tokens" not in kwargs:
        return None
    error_text = " ".join(
        str(value or "")
        for value in (
            error,
            getattr(error, "body", None),
            getattr(error, "param", None),
        )
    ).lower()
    if "max_tokens" not in error_text and "max tokens" not in error_text:
        return None
    retry_kwargs = dict(kwargs)
    retry_kwargs.pop("max_tokens", None)
    retry_kwargs["max_completion_tokens"] = max_tokens
    return retry_kwargs


def _fallback_reason(error: Exception, requested_provider: str) -> Optional[str]:
    """Return the permitted fallback reason for auto-routed calls."""
    if requested_provider not in ("auto", "", None):
        return None
    if _is_payment_error(error):
        return "payment error"
    if _is_connection_error(error):
        return "connection error"
    return None


def _prepare_fallback_call(
    *,
    target: AuxiliaryCallTarget,
    task: Optional[str],
    reason: str,
    messages: list,
    temperature: Optional[float],
    max_tokens: Optional[int],
    tools: Optional[list],
    timeout: float,
    extra_body: Optional[dict],
) -> Optional[AuxiliaryFallbackCall]:
    client, model, provider = _try_provider_fallback(
        target.active_provider,
        task,
        reason=reason,
    )
    if client is None or not model:
        return None
    base_url = str(getattr(client, "base_url", "") or "")
    kwargs = _build_call_kwargs(
        provider,
        model,
        messages,
        temperature=temperature,
        max_tokens=max_tokens,
        tools=tools,
        timeout=timeout,
        extra_body=extra_body,
        base_url=base_url,
    )
    return AuxiliaryFallbackCall(
        provider=provider,
        model=model,
        client=client,
        kwargs=kwargs,
    )


def _validate_llm_response(response: Any, task: str = None) -> Any:
    """Validate that an LLM response has the expected .choices[0].message shape.

    Fails fast with a clear error instead of letting malformed payloads
    propagate to downstream consumers where they crash with misleading
    AttributeError (e.g. "'str' object has no attribute 'choices'").

    See #7264.
    """
    if response is None:
        raise RuntimeError(
            f"Auxiliary {task or 'call'}: LLM returned None response"
        )
    try:
        choices = response.choices
        if not choices or not hasattr(choices[0], "message"):
            raise AttributeError("missing choices[0].message")
    except (AttributeError, TypeError, IndexError) as exc:
        response_type = type(response).__name__
        response_preview = str(response)[:120]
        raise RuntimeError(
            f"Auxiliary {task or 'call'}: LLM returned invalid response "
            f"(type={response_type}): {response_preview!r}. "
            f"Expected object with .choices[0].message — check provider "
            f"adapter or custom endpoint compatibility."
        ) from exc
    return response


def _execute_sync_auxiliary_call(
    *,
    target: AuxiliaryCallTarget,
    task: Optional[str],
    kwargs: Dict[str, Any],
    messages: list,
    temperature: Optional[float],
    max_tokens: Optional[int],
    tools: Optional[list],
    timeout: float,
    extra_body: Optional[dict],
) -> Any:
    try:
        return _validate_llm_response(
            target.client.chat.completions.create(**kwargs),
            task,
        )
    except Exception as first_error:
        retry_kwargs = _completion_token_retry_kwargs(
            kwargs,
            first_error,
            max_tokens,
        )
        if retry_kwargs is not None:
            try:
                return _validate_llm_response(
                    target.client.chat.completions.create(**retry_kwargs),
                    task,
                )
            except Exception as retry_error:
                first_error = retry_error

        reason = _fallback_reason(first_error, target.requested_provider)
        if reason is not None:
            logger.info(
                "Auxiliary %s: %s on %s (%s), trying fallback",
                task or "call",
                reason,
                target.active_provider,
                first_error,
            )
            fallback = _prepare_fallback_call(
                target=target,
                task=task,
                reason=reason,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
                tools=tools,
                timeout=timeout,
                extra_body=extra_body,
            )
            if fallback is not None:
                return _validate_llm_response(
                    fallback.client.chat.completions.create(**fallback.kwargs),
                    task,
                )
        raise first_error


async def _execute_async_auxiliary_call(
    *,
    target: AuxiliaryCallTarget,
    task: Optional[str],
    kwargs: Dict[str, Any],
    messages: list,
    temperature: Optional[float],
    max_tokens: Optional[int],
    tools: Optional[list],
    timeout: float,
    extra_body: Optional[dict],
) -> Any:
    try:
        return _validate_llm_response(
            await target.client.chat.completions.create(**kwargs),
            task,
        )
    except Exception as first_error:
        retry_kwargs = _completion_token_retry_kwargs(
            kwargs,
            first_error,
            max_tokens,
        )
        if retry_kwargs is not None:
            try:
                return _validate_llm_response(
                    await target.client.chat.completions.create(**retry_kwargs),
                    task,
                )
            except Exception as retry_error:
                first_error = retry_error

        reason = _fallback_reason(first_error, target.requested_provider)
        if reason is not None:
            logger.info(
                "Auxiliary %s (async): %s on %s (%s), trying fallback",
                task or "call",
                reason,
                target.active_provider,
                first_error,
            )
            fallback = _prepare_fallback_call(
                target=target,
                task=task,
                reason=reason,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
                tools=tools,
                timeout=timeout,
                extra_body=extra_body,
            )
            if fallback is not None:
                async_client, async_model = _to_async_client(
                    fallback.client,
                    fallback.model,
                )
                fallback_kwargs = dict(fallback.kwargs)
                if async_model and async_model != fallback_kwargs.get("model"):
                    fallback_kwargs["model"] = async_model
                return _validate_llm_response(
                    await async_client.chat.completions.create(**fallback_kwargs),
                    task,
                )
        raise first_error


def call_llm(
    task: str = None,
    *,
    provider: str = None,
    model: str = None,
    base_url: str = None,
    api_key: str = None,
    main_runtime: Optional[Dict[str, Any]] = None,
    messages: list,
    temperature: float = None,
    max_tokens: int = None,
    tools: list = None,
    timeout: float = None,
    extra_body: dict = None,
) -> Any:
    """Centralized synchronous LLM call.

    Resolves provider + model (from task config, explicit args, or auto-detect),
    handles auth, request formatting, and model-specific arg adjustments.

    Args:
        task: Auxiliary task name ("compression", "vision", "web_extract",
              "session_search", "skills_hub", or "mcp").
              Reads provider:model from config/env. Ignored if provider is set.
        provider: Explicit provider override.
        model: Explicit model override.
        messages: Chat messages list.
        temperature: Sampling temperature (None = provider default).
        max_tokens: Max output tokens (handles max_tokens vs max_completion_tokens).
        tools: Tool definitions (for function calling).
        timeout: Request timeout in seconds (None = read from auxiliary.{task}.timeout config).
        extra_body: Additional request body fields.

    Returns:
        Response object with .choices[0].message.content

    Raises:
        RuntimeError: If no provider is configured.
    """
    target = _resolve_auxiliary_call_target(
        task=task,
        provider=provider,
        model=model,
        base_url=base_url,
        api_key=api_key,
        main_runtime=main_runtime,
        async_mode=False,
    )
    effective_timeout = timeout if timeout is not None else _get_task_timeout(task)
    if task:
        logger.info(
            "Auxiliary %s: using %s (%s)%s",
            task,
            target.active_provider,
            target.model,
            (
                f" at {target.base_url}"
                if target.base_url and "openrouter" not in target.base_url.lower()
                else ""
            ),
        )

    kwargs = _build_call_kwargs(
        target.active_provider,
        target.model,
        messages,
        temperature=temperature,
        max_tokens=max_tokens,
        tools=tools,
        timeout=effective_timeout,
        extra_body=extra_body,
        base_url=target.base_url,
    )
    return _execute_sync_auxiliary_call(
        target=target,
        task=task,
        kwargs=kwargs,
        messages=messages,
        temperature=temperature,
        max_tokens=max_tokens,
        tools=tools,
        timeout=effective_timeout,
        extra_body=extra_body,
    )


def extract_content_or_reasoning(response) -> str:
    """Extract visible content or reasoning through the shared response contract."""
    return visible_or_reasoning_text(response.choices[0].message)


async def async_call_llm(
    task: str = None,
    *,
    provider: str = None,
    model: str = None,
    base_url: str = None,
    api_key: str = None,
    main_runtime: Optional[Dict[str, Any]] = None,
    messages: list,
    temperature: float = None,
    max_tokens: int = None,
    tools: list = None,
    timeout: float = None,
    extra_body: dict = None,
) -> Any:
    """Centralized asynchronous LLM call.

    Same as call_llm() but async. See call_llm() for full documentation.
    """
    target = _resolve_auxiliary_call_target(
        task=task,
        provider=provider,
        model=model,
        base_url=base_url,
        api_key=api_key,
        main_runtime=main_runtime,
        async_mode=True,
    )
    effective_timeout = timeout if timeout is not None else _get_task_timeout(task)

    kwargs = _build_call_kwargs(
        target.active_provider,
        target.model,
        messages,
        temperature=temperature,
        max_tokens=max_tokens,
        tools=tools,
        timeout=effective_timeout,
        extra_body=extra_body,
        base_url=target.base_url,
    )
    return await _execute_async_auxiliary_call(
        target=target,
        task=task,
        kwargs=kwargs,
        messages=messages,
        temperature=temperature,
        max_tokens=max_tokens,
        tools=tools,
        timeout=effective_timeout,
        extra_body=extra_body,
    )

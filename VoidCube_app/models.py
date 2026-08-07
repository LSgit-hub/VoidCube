"""Shared live model discovery and lightweight validation helpers."""

from __future__ import annotations

import json
import os
import urllib.request
import urllib.error
from difflib import get_close_matches
from typing import Any, Optional

from agent.integration_policy import contains_retired_integration

_openrouter_catalog_cache: list[tuple[str, str]] | None = None

# ---------------------------------------------------------------------------
# Nous Portal account tier detection
# ---------------------------------------------------------------------------

def fetch_nous_account_tier(access_token: str, portal_base_url: str = "") -> dict[str, Any]:
    """Fetch the user's Nous Portal account/subscription info.

    Calls ``<portal>/api/oauth/account`` with the OAuth access token.

    Returns the parsed JSON dict on success, e.g.::

        {
            "subscription": {
                "plan": "Plus",
                "tier": 2,
                "monthly_charge": 20,
                "credits_remaining": 1686.60,
                ...
            },
            ...
        }

    Returns an empty dict on any failure (network, auth, parse).
    """
    base = (portal_base_url or "https://portal.nousresearch.com").rstrip("/")
    url = f"{base}/api/oauth/account"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Accept": "application/json",
    }
    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=8) as resp:
            return json.loads(resp.read().decode())
    except Exception:
        return {}


def is_nous_free_tier(account_info: dict[str, Any]) -> bool:
    """Return True if the account info indicates a free (unpaid) tier.

    Checks ``subscription.monthly_charge == 0``.  Returns False when
    the field is missing or unparseable (assumes paid — don't block users).
    """
    sub = account_info.get("subscription")
    if not isinstance(sub, dict):
        return False
    charge = sub.get("monthly_charge")
    if charge is None:
        return False
    try:
        return float(charge) == 0
    except (TypeError, ValueError):
        return False


def partition_nous_models_by_tier(
    model_ids: list[str],
    pricing: dict[str, dict[str, str]],
    free_tier: bool,
) -> tuple[list[str], list[str]]:
    """Split Nous models into (selectable, unavailable) based on user tier.

    For paid-tier users: all models are selectable, none unavailable
    (free-model filtering is handled separately by ``filter_nous_free_models``).

    For free-tier users: only free models are selectable; paid models
    are returned as unavailable (shown grayed out in the menu).
    """
    if not free_tier:
        return (model_ids, [])

    if not pricing:
        return (model_ids, [])  # can't determine, show everything

    selectable: list[str] = []
    unavailable: list[str] = []
    for mid in model_ids:
        if _is_model_free(mid, pricing):
            selectable.append(mid)
        else:
            unavailable.append(mid)
    return (selectable, unavailable)


# ---------------------------------------------------------------------------
# TTL cache for free-tier detection — avoids repeated API calls within a
# session while still picking up upgrades quickly.
# ---------------------------------------------------------------------------
_FREE_TIER_CACHE_TTL: int = 180  # seconds (3 minutes)
_free_tier_cache: tuple[bool, float] | None = None  # (result, timestamp)


def check_nous_free_tier() -> bool:
    """Check if the current Nous Portal user is on a free (unpaid) tier.

    Results are cached for ``_FREE_TIER_CACHE_TTL`` seconds to avoid
    hitting the Portal API on every call.  The cache is short-lived so
    that an account upgrade is reflected within a few minutes.

    Returns False (assume paid) on any error — never blocks paying users.
    """
    global _free_tier_cache
    import time

    now = time.monotonic()
    if _free_tier_cache is not None:
        cached_result, cached_at = _free_tier_cache
        if now - cached_at < _FREE_TIER_CACHE_TTL:
            return cached_result

    try:
        from VoidCube_app.provider_auth import get_provider_auth_state, resolve_nous_runtime_credentials

        # Ensure we have a fresh token (triggers refresh if needed)
        resolve_nous_runtime_credentials(min_key_ttl_seconds=60)

        state = get_provider_auth_state("nous")
        if not state:
            _free_tier_cache = (False, now)
            return False
        access_token = state.get("access_token", "")
        portal_url = state.get("portal_base_url", "")
        if not access_token:
            _free_tier_cache = (False, now)
            return False

        account_info = fetch_nous_account_tier(access_token, portal_url)
        result = is_nous_free_tier(account_info)
        _free_tier_cache = (result, now)
        return result
    except Exception:
        _free_tier_cache = (False, now)
        return False  # default to paid on error — don't block users


_PROVIDER_LABELS = {
    "openrouter": "OpenRouter",
    "openai": "OpenAI",
    "copilot-acp": "GitHub Copilot ACP",
    "nous": "Nous Portal",
    "zai": "Z.AI / GLM",
    "kimi-coding": "Kimi / Moonshot",
    "minimax": "MiniMax",
    "minimax-cn": "MiniMax (China)",
    "deepseek": "DeepSeek",
    "agnes-ai": "Agnes-AI",
    "qwen-oauth": "Qwen OAuth (Portal)",
    "custom": "Custom endpoint",
}

_PROVIDER_ALIASES = {
    "glm": "zai",
    "z-ai": "zai",
    "z.ai": "zai",
    "zhipu": "zai",
    "github-copilot-acp": "copilot-acp",
    "copilot-acp-agent": "copilot-acp",
    "kimi": "kimi-coding",
    "moonshot": "kimi-coding",
    "minimax-china": "minimax-cn",
    "minimax_cn": "minimax-cn",
    "deep-seek": "deepseek",
    "qwen-portal": "qwen-oauth",
}

_PROVIDER_ORDER = (
    "openrouter",
    "nous",
    "openai",
    "deepseek",
    "agnes-ai",
    "zai",
    "kimi-coding",
    "minimax",
    "minimax-cn",
    "qwen-oauth",
    "copilot-acp",
    "custom",
)


def _openrouter_model_is_free(pricing: Any) -> bool:
    """Return True when both prompt and completion pricing are zero."""
    if not isinstance(pricing, dict):
        return False
    try:
        return float(pricing.get("prompt", "0")) == 0 and float(pricing.get("completion", "0")) == 0
    except (TypeError, ValueError):
        return False


def _clean_model_id(model_id: str) -> str:
    """Clean model ID by removing duplicate suffixes like ':free:free'."""
    if not model_id:
        return model_id
    
    # Remove duplicate :free suffixes
    while ":free:free" in model_id:
        model_id = model_id.replace(":free:free", ":free")
    
    # Remove duplicate :extended suffixes  
    while ":extended:extended" in model_id:
        model_id = model_id.replace(":extended:extended", ":extended")
    
    # Remove duplicate :fast suffixes
    while ":fast:fast" in model_id:
        model_id = model_id.replace(":fast:fast", ":fast")
    
    return model_id


def fetch_openrouter_models(
    timeout: float = 8.0,
    *,
    force_refresh: bool = False,
) -> list[tuple[str, str]]:
    """Return the live OpenRouter model list without a static fallback."""
    global _openrouter_catalog_cache

    if _openrouter_catalog_cache is not None and not force_refresh:
        return list(_openrouter_catalog_cache)

    try:
        req = urllib.request.Request(
            "https://openrouter.ai/api/v1/models",
            headers={"Accept": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            payload = json.loads(resp.read().decode())
    except Exception:
        return []

    live_items = payload.get("data", [])
    if not isinstance(live_items, list):
        return []

    live_models: list[tuple[str, str]] = []
    seen_ids: set[str] = set()
    for item in live_items:
        if not isinstance(item, dict):
            continue
        mid = str(item.get("id") or "").strip()
        if not mid:
            continue
        
        # Clean model ID to remove duplicate suffixes like ':free:free'
        mid = _clean_model_id(mid)
        if contains_retired_integration(mid):
            continue
        
        if mid in seen_ids:
            continue
        seen_ids.add(mid)
        description = "free" if _openrouter_model_is_free(item.get("pricing")) else ""
        live_models.append((mid, description))

    _openrouter_catalog_cache = live_models
    return list(live_models)


def model_ids(*, force_refresh: bool = False) -> list[str]:
    """Return just the OpenRouter model-id strings."""
    return [mid for mid, _ in fetch_openrouter_models(force_refresh=force_refresh)]


def menu_labels(*, force_refresh: bool = False) -> list[str]:
    """Return display labels like 'deepseek/deepseek-chat (recommended)'."""
    labels = []
    for mid, desc in fetch_openrouter_models(force_refresh=force_refresh):
        labels.append(f"{mid} ({desc})" if desc else mid)
    return labels



# ---------------------------------------------------------------------------
# Pricing helpers — fetch live pricing from OpenRouter-compatible /v1/models
# ---------------------------------------------------------------------------

# Cache: maps model_id → {"prompt": str, "completion": str} per endpoint
_pricing_cache: dict[str, dict[str, dict[str, str]]] = {}


def _format_price_per_mtok(per_token_str: str) -> str:
    """Convert a per-token price string to a human-friendly $/Mtok string.

    Always uses 2 decimal places so that prices align vertically when
    right-justified in a column (the decimal point stays in the same position).

    Examples:
        "0.000003"   → "$3.00"      (per million tokens)
        "0.00003"    → "$30.00"
        "0.00000015" → "$0.15"
        "0.0000001"  → "$0.10"
        "0.00018"    → "$180.00"
        "0"          → "free"
    """
    try:
        val = float(per_token_str)
    except (TypeError, ValueError):
        return "?"
    if val == 0:
        return "free"
    per_m = val * 1_000_000
    return f"${per_m:.2f}"


def format_model_pricing_table(
    models: list[tuple[str, str]],
    pricing_map: dict[str, dict[str, str]],
    current_model: str = "",
    indent: str = "      ",
) -> list[str]:
    """Build a column-aligned model+pricing table for terminal display.

    Returns a list of pre-formatted lines ready to print.
    *models* is ``[(model_id, description), ...]``.
    """
    if not models:
        return []

    # Build rows: (model_id, input_price, output_price, cache_price, is_current)
    rows: list[tuple[str, str, str, str, bool]] = []
    has_cache = False
    for mid, _desc in models:
        is_cur = mid == current_model
        p = pricing_map.get(mid)
        if p:
            inp = _format_price_per_mtok(p.get("prompt", ""))
            out = _format_price_per_mtok(p.get("completion", ""))
            cache_read = p.get("input_cache_read", "")
            cache = _format_price_per_mtok(cache_read) if cache_read else ""
            if cache:
                has_cache = True
        else:
            inp, out, cache = "", "", ""
        rows.append((mid, inp, out, cache, is_cur))

    name_col = max(len(r[0]) for r in rows) + 2
    # Compute price column widths from the actual data so decimals align
    price_col = max(
        max((len(r[1]) for r in rows if r[1]), default=4),
        max((len(r[2]) for r in rows if r[2]), default=4),
        3,  # minimum: "In" / "Out" header
    )
    cache_col = max(
        max((len(r[3]) for r in rows if r[3]), default=4),
        5,  # minimum: "Cache" header
    ) if has_cache else 0
    lines: list[str] = []

    # Header
    if has_cache:
        lines.append(f"{indent}{'Model':<{name_col}} {'In':>{price_col}}  {'Out':>{price_col}}  {'Cache':>{cache_col}}  /Mtok")
        lines.append(f"{indent}{'-' * name_col} {'-' * price_col}  {'-' * price_col}  {'-' * cache_col}")
    else:
        lines.append(f"{indent}{'Model':<{name_col}} {'In':>{price_col}}  {'Out':>{price_col}}  /Mtok")
        lines.append(f"{indent}{'-' * name_col} {'-' * price_col}  {'-' * price_col}")

    for mid, inp, out, cache, is_cur in rows:
        marker = "  ← current" if is_cur else ""
        if has_cache:
            lines.append(f"{indent}{mid:<{name_col}} {inp:>{price_col}}  {out:>{price_col}}  {cache:>{cache_col}}{marker}")
        else:
            lines.append(f"{indent}{mid:<{name_col}} {inp:>{price_col}}  {out:>{price_col}}{marker}")

    return lines


def fetch_models_with_pricing(
    api_key: str | None = None,
    base_url: str = "https://openrouter.ai/api",
    timeout: float = 8.0,
    *,
    force_refresh: bool = False,
) -> dict[str, dict[str, str]]:
    """Fetch ``/v1/models`` and return ``{model_id: {prompt, completion}}`` pricing.

    Results are cached per *base_url* so repeated calls are free.
    Works with any OpenRouter-compatible endpoint (OpenRouter, Nous Portal).
    """
    cache_key = (base_url or "").rstrip("/")
    if not force_refresh and cache_key in _pricing_cache:
        return _pricing_cache[cache_key]

    url = cache_key.rstrip("/") + "/v1/models"
    headers: dict[str, str] = {"Accept": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            payload = json.loads(resp.read().decode())
    except Exception:
        _pricing_cache[cache_key] = {}
        return {}

    result: dict[str, dict[str, str]] = {}
    for item in payload.get("data", []):
        mid = item.get("id")
        pricing = item.get("pricing")
        if mid and isinstance(pricing, dict):
            entry: dict[str, str] = {
                "prompt": str(pricing.get("prompt", "")),
                "completion": str(pricing.get("completion", "")),
            }
            if pricing.get("input_cache_read"):
                entry["input_cache_read"] = str(pricing["input_cache_read"])
            if pricing.get("input_cache_write"):
                entry["input_cache_write"] = str(pricing["input_cache_write"])
            result[mid] = entry

    _pricing_cache[cache_key] = result
    return result


def _resolve_openrouter_api_key() -> str:
    """Best-effort OpenRouter API key for pricing fetch."""
    return os.getenv("OPENROUTER_API_KEY", "").strip()


def _resolve_nous_pricing_credentials() -> tuple[str, str]:
    """Return ``(api_key, base_url)`` for Nous Portal pricing, or empty strings."""
    try:
        from VoidCube_app.provider_auth import resolve_nous_runtime_credentials
        creds = resolve_nous_runtime_credentials()
        if creds:
            return (creds.get("api_key", ""), creds.get("base_url", ""))
    except Exception:
        pass
    return ("", "")


def get_pricing_for_provider(provider: str, *, force_refresh: bool = False) -> dict[str, dict[str, str]]:
    """Return live pricing for providers that support it (openrouter, nous)."""
    normalized = normalize_provider(provider)
    if normalized == "openrouter":
        return fetch_models_with_pricing(
            api_key=_resolve_openrouter_api_key(),
            base_url="https://openrouter.ai/api",
            force_refresh=force_refresh,
        )
    if normalized == "nous":
        api_key, base_url = _resolve_nous_pricing_credentials()
        if base_url:
            # Nous base_url typically looks like https://inference-api.nousresearch.com/v1
            # We need the part before /v1 for our fetch function
            stripped = base_url.rstrip("/")
            if stripped.endswith("/v1"):
                stripped = stripped[:-3]
            return fetch_models_with_pricing(
                api_key=api_key,
                base_url=stripped,
                force_refresh=force_refresh,
            )
    return {}


# All provider IDs and aliases that are valid for the provider:model syntax.
_KNOWN_PROVIDER_NAMES: set[str] = (
    set(_PROVIDER_LABELS.keys())
    | set(_PROVIDER_ALIASES.keys())
    | {"openrouter", "custom"}
)


def list_available_providers() -> list[dict[str, str]]:
    """Return info about all providers the user could use with ``provider:model``.

    Each dict has ``id``, ``label``, and ``aliases``.
    Checks which providers have valid credentials configured.
    """
    # Build reverse alias map
    aliases_for: dict[str, list[str]] = {}
    for alias, canonical in _PROVIDER_ALIASES.items():
        aliases_for.setdefault(canonical, []).append(alias)

    result = []
    for pid in _PROVIDER_ORDER:
        label = _PROVIDER_LABELS.get(pid, pid)
        alias_list = aliases_for.get(pid, [])
        # Check if this provider has credentials available
        has_creds = False
        try:
            from VoidCube_app.provider_auth import get_auth_status, has_usable_secret
            if pid == "custom":
                custom_base_url = _get_custom_base_url() or ""
                has_creds = bool(custom_base_url.strip())
            elif pid == "openrouter":
                has_creds = has_usable_secret(os.getenv("OPENROUTER_API_KEY", ""))
            else:
                status = get_auth_status(pid)
                has_creds = bool(status.get("logged_in") or status.get("configured"))
        except Exception:
            pass
        result.append({
            "id": pid,
            "label": label,
            "aliases": alias_list,
            "authenticated": has_creds,
        })
    return result


def parse_model_input(raw: str, current_provider: str) -> tuple[str, str]:
    """Parse ``/model`` input into ``(provider, model)``.

    Supports ``provider:model`` syntax to switch providers at runtime::

        openrouter:deepseek/deepseek-chat  →  ("openrouter", "deepseek/deepseek-chat")
        nous:VoidCube-3                           →  ("nous", "VoidCube-3")
        deepseek/deepseek-chat             →  (current_provider, "deepseek/deepseek-chat")
        gpt-5.4                                 →  (current_provider, "gpt-5.4")

    The colon is only treated as a provider delimiter if the left side is a
    recognized provider name or alias.  This avoids misinterpreting model names
    that happen to contain colons (e.g. ``deepseek/deepseek-chat:v1``).

    Returns ``(provider, model)`` where *provider* is either the explicit
    provider from the input or *current_provider* if none was specified.
    """
    stripped = raw.strip()
    colon = stripped.find(":")
    if colon > 0:
        provider_part = stripped[:colon].strip().lower()
        model_part = stripped[colon + 1:].strip()
        if provider_part and model_part and provider_part in _KNOWN_PROVIDER_NAMES:
            # Support custom:name:model triple syntax for named custom
            # providers.  ``custom:local:qwen`` → ("custom:local", "qwen").
            # Single colon ``custom:qwen`` → ("custom", "qwen") as before.
            if provider_part == "custom" and ":" in model_part:
                second_colon = model_part.find(":")
                custom_name = model_part[:second_colon].strip()
                actual_model = model_part[second_colon + 1:].strip()
                if custom_name and actual_model:
                    return (f"custom:{custom_name}", actual_model)
            return (normalize_provider(provider_part), model_part)
    return (current_provider, stripped)


def _get_custom_base_url() -> str:
    """Get the custom endpoint base_url from config.yaml."""
    try:
        from VoidCube_app.config import (
            get_active_model_config,
            get_active_provider_key,
            load_config,
        )
        config = load_config()
        provider_key = get_active_provider_key(config)
        if provider_key != "custom":
            return ""
        model_cfg = get_active_model_config(config)
        return str(model_cfg.get("base_url", "")).strip()
    except Exception:
        pass
    return ""


def curated_models_for_provider(
    provider: Optional[str],
    *,
    force_refresh: bool = False,
) -> list[tuple[str, str]]:
    """Return ``(model_id, description)`` tuples for a provider's model list.

    Model names are accepted only from the configured provider's live API.
    An unavailable API deliberately yields an empty list so callers can offer
    manual model input instead of presenting stale names.
    """
    normalized = normalize_provider(provider)
    if normalized == "openrouter":
        return fetch_openrouter_models(force_refresh=force_refresh)
    return [(model_id, "") for model_id in provider_model_ids(normalized)]


def detect_provider_for_model(
    model_name: str,
    current_provider: str,
) -> Optional[tuple[str, str]]:
    """Resolve a model only when it matches the live OpenRouter catalog."""
    name = (model_name or "").strip()
    if not name:
        return None

    # Model names are not assigned to direct providers from static heuristics.
    # The only automatic remapping is an exact match from the live OpenRouter
    # catalog, which is safe because it carries the provider/model identifier.
    or_slug = _find_openrouter_slug(name)
    if or_slug:
        if current_provider != "openrouter":
            return ("openrouter", or_slug)
        # Already on openrouter, just return the resolved slug
        if or_slug != name:
            return ("openrouter", or_slug)
        return None  # already on openrouter with matching name

    return None


def _find_openrouter_slug(model_name: str) -> Optional[str]:
    """Find the full OpenRouter model slug for a bare or partial model name.

    Handles:
    - Exact match: ``deepseek/deepseek-chat`` → as-is
    - Bare name: ``deepseek-chat`` → ``deepseek/deepseek-chat``
    """
    name_lower = model_name.strip().lower()
    if not name_lower:
        return None

    # Exact match (already has provider/ prefix)
    for mid in model_ids():
        if name_lower == mid.lower():
            return mid

    # Try matching just the model part (after the /)
    for mid in model_ids():
        if "/" in mid:
            _, model_part = mid.split("/", 1)
            if name_lower == model_part.lower():
                return mid

    return None


def normalize_provider(provider: Optional[str]) -> str:
    """Normalize provider aliases to Voidcube' canonical provider ids.

    Note: ``"auto"`` passes through unchanged — use
    ``VoidCube_app.provider_auth.resolve_provider()`` to resolve it to a concrete
    provider based on credentials and environment.
    """
    normalized = (provider or "openrouter").strip().lower()
    return _PROVIDER_ALIASES.get(normalized, normalized)


def provider_label(provider: Optional[str]) -> str:
    """Return a human-friendly label for a provider id or alias."""
    original = (provider or "openrouter").strip()
    normalized = original.lower()
    if normalized == "auto":
        return "Auto"
    normalized = normalize_provider(normalized)
    return _PROVIDER_LABELS.get(normalized, original or "OpenRouter")


def model_supports_fast_mode(model_id: Optional[str]) -> bool:
    """Return whether provider metadata confirms priority processing support.

    The generic ``/models`` protocol does not expose this capability, so the
    CLI does not infer it from a hard-coded model-name list.
    """
    return False


def resolve_fast_mode_overrides(model_id: Optional[str]) -> dict[str, Any] | None:
    """Return request_overrides for priority mode, or None if unsupported.

    Returns provider-appropriate overrides:
    - OpenAI models: ``{"service_tier": "priority"}`` (Priority Processing)

    The overrides are injected into the API request kwargs by
    ``build_chat_completion_kwargs`` in ``agent.api_request``.
    """
    if not model_supports_fast_mode(model_id):
        return None
    return {"service_tier": "priority"}


def _provider_model_ids_unfiltered(
    provider: Optional[str],
    *,
    force_refresh: bool = False,
) -> list[str]:
    """Return model IDs reported by the requested provider's live API."""
    normalized = normalize_provider(provider)
    if normalized == "openrouter":
        return model_ids(force_refresh=force_refresh)
    if normalized == "copilot-acp":
        return []

    try:
        from VoidCube_app.runtime_provider import resolve_runtime_provider

        runtime = resolve_runtime_provider(requested=normalized)
    except Exception:
        return []

    base_url = str(runtime.get("base_url") or "").strip()
    if not base_url:
        return []
    api_key = str(runtime.get("api_key") or "").strip()
    return fetch_api_models(api_key, base_url) or []


def provider_model_ids(provider: Optional[str], *, force_refresh: bool = False) -> list[str]:
    """Return visible provider models after enforcing project integration policy."""
    return [
        model_id
        for model_id in _provider_model_ids_unfiltered(
            provider,
            force_refresh=force_refresh,
        )
        if not contains_retired_integration(model_id)
    ]


def _payload_items(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict):
        for key in ("data", "models"):
            data = payload.get(key)
            if isinstance(data, list):
                return [item for item in data if isinstance(item, dict)]
    return []


def probe_api_models(
    api_key: Optional[str],
    base_url: Optional[str],
    timeout: float = 5.0,
) -> dict[str, Any]:
    """Probe an OpenAI-compatible ``/models`` endpoint with light URL heuristics."""
    normalized = (base_url or "").strip().rstrip("/")
    if not normalized:
        return {
            "models": None,
            "probed_url": None,
            "resolved_base_url": "",
            "suggested_base_url": None,
            "used_fallback": False,
        }

    if normalized.endswith("/v1"):
        alternate_base = normalized[:-3].rstrip("/")
    else:
        alternate_base = normalized + "/v1"

    candidates: list[tuple[str, bool]] = [(normalized, False)]
    if alternate_base and alternate_base != normalized:
        candidates.append((alternate_base, True))

    tried: list[str] = []
    headers: dict[str, str] = {}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    for candidate_base, is_fallback in candidates:
        url = candidate_base.rstrip("/") + "/models"
        tried.append(url)
        req = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = json.loads(resp.read().decode())
                items = _payload_items(data)
                model_ids: list[str] = []
                seen_ids: set[str] = set()
                for item in items:
                    model_id = str(item.get("id") or item.get("name") or "").strip()
                    if model_id and model_id not in seen_ids:
                        seen_ids.add(model_id)
                        model_ids.append(model_id)
                return {
                    "models": model_ids,
                    "probed_url": url,
                    "resolved_base_url": candidate_base.rstrip("/"),
                    "suggested_base_url": alternate_base if alternate_base != candidate_base else normalized,
                    "used_fallback": is_fallback,
                }
        except Exception:
            continue

    return {
        "models": None,
        "probed_url": tried[0] if tried else normalized.rstrip("/") + "/models",
        "resolved_base_url": normalized,
        "suggested_base_url": alternate_base if alternate_base != normalized else None,
        "used_fallback": False,
    }


def fetch_api_models(
    api_key: Optional[str],
    base_url: Optional[str],
    timeout: float = 5.0,
) -> Optional[list[str]]:
    """Fetch the list of available model IDs from the provider's ``/models`` endpoint.

    Returns a list of model ID strings, or ``None`` if the endpoint could not
    be reached (network error, timeout, auth failure, etc.).
    """
    return probe_api_models(api_key, base_url, timeout=timeout).get("models")


def validate_requested_model(
    model_name: str,
    provider: Optional[str],
    *,
    api_key: Optional[str] = None,
    base_url: Optional[str] = None,
) -> dict[str, Any]:
    """
    Validate a ``/model`` value for the active provider.

    Performs format checks first, then probes the live API to confirm
    the model actually exists.

    Returns a dict with:
      - accepted: whether the CLI should switch to the requested model now
      - persist: whether it is safe to save to config
      - recognized: whether it matched a known provider catalog
      - message: optional warning / guidance for the user
    """
    requested = (model_name or "").strip()
    normalized = normalize_provider(provider)
    if normalized == "openrouter" and base_url and "openrouter.ai" not in base_url:
        normalized = "custom"
    requested_for_lookup = requested

    if not requested:
        return {
            "accepted": False,
            "persist": False,
            "recognized": False,
            "message": "Model name cannot be empty.",
        }

    if any(ch.isspace() for ch in requested):
        return {
            "accepted": False,
            "persist": False,
            "recognized": False,
            "message": "Model names cannot contain spaces.",
        }

    if normalized == "custom":
        probe = probe_api_models(api_key, base_url)
        api_models = probe.get("models")
        if api_models is not None:
            if requested_for_lookup in set(api_models):
                return {
                    "accepted": True,
                    "persist": True,
                    "recognized": True,
                    "message": None,
                }

            suggestions = get_close_matches(requested, api_models, n=3, cutoff=0.5)
            suggestion_text = ""
            if suggestions:
                suggestion_text = "\n  Similar models: " + ", ".join(f"`{s}`" for s in suggestions)

            message = (
                f"`{requested}` was not found in this custom endpoint's model listing "
                f"({probe.get('probed_url')})."
                f"{suggestion_text}"
            )
            if probe.get("used_fallback"):
                message += (
                    f"\n  Endpoint verification succeeded after trying `{probe.get('resolved_base_url')}`. "
                    f"Consider saving that as your base URL."
                )

            return {
                "accepted": False,
                "persist": False,
                "recognized": False,
                "message": message,
            }

        message = (
            f"Note: could not reach this custom endpoint's model listing at `{probe.get('probed_url')}`. "
            f"Voidcube will still save `{requested}`, but the endpoint should expose `/models` for verification."
        )
        if probe.get("suggested_base_url"):
            message += f"\n  If this server expects `/v1`, try base URL: `{probe.get('suggested_base_url')}`"

        return {
            "accepted": True,
            "persist": True,
            "recognized": False,
            "message": message,
        }

    # Probe the live API to check if the model actually exists
    api_models = fetch_api_models(api_key, base_url)

    if api_models is not None:
        if requested_for_lookup in set(api_models):
            # API confirmed the model exists
            return {
                "accepted": True,
                "persist": True,
                "recognized": True,
                "message": None,
            }
        suggestions = get_close_matches(requested, api_models, n=3, cutoff=0.5)
        suggestion_text = ""
        if suggestions:
            suggestion_text = "\n  Similar models: " + ", ".join(f"`{s}`" for s in suggestions)

        return {
            "accepted": False,
            "persist": False,
            "recognized": False,
            "message": (
                f"`{requested}` was not found in this provider's model listing."
                f"{suggestion_text}"
            ),
        }

    # api_models is None — couldn't reach API.  Accept and persist,
    # but warn so typos don't silently break things.
    provider_label = _PROVIDER_LABELS.get(normalized, normalized)
    return {
        "accepted": True,
        "persist": True,
        "recognized": False,
        "message": (
            f"Could not reach the {provider_label} API to validate `{requested}`. "
            f"If the service isn't down, this model may not be valid."
        ),
    }

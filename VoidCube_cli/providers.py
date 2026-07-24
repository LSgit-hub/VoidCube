"""
Single source of truth for provider identity in Voidcube Agent.

Two data sources, merged at runtime:

1. **Active runtime providers** — the runtime allowlist limits which
   models.dev entries can become built-in providers.

2. **Voidcube overlays** — auth patterns, aggregator flags,
   and additional env vars that models.dev doesn't track.  Small dict,
   maintained here.

3. **User config** (``providers:`` section in config.yaml) — user-defined
   endpoints and overrides.  Merged on top of everything else.

Other modules import from this file.  No parallel registries.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from VoidCube_cli.auth import RUNTIME_PROVIDER_IDS

logger = logging.getLogger(__name__)


# -- Voidcube overlay ----------------------------------------------------------
# Voidcube-specific metadata that models.dev doesn't provide.

@dataclass(frozen=True)
class VoidcubeOverlay:
    """Voidcube-specific provider metadata layered on top of models.dev.
    
    .. deprecated::
        Use VoidCube_cli.provider_registry.ProviderEntry instead.
        VoidcubeOverlay is retained as a data source for ProviderRegistry.
    """

    is_aggregator: bool = False
    auth_type: str = "api_key"            # api_key | oauth_device_code | oauth_external | external_process
    extra_env_vars: Tuple[str, ...] = ()  # env vars models.dev doesn't list
    base_url_override: str = ""           # override if models.dev URL is wrong/missing
    base_url_env_var: str = ""            # env var for user-custom base URL


VOIDCUBE_OVERLAYS: Dict[str, VoidcubeOverlay] = {
    "openrouter": VoidcubeOverlay(
        is_aggregator=True,
        extra_env_vars=("OPENAI_API_KEY",),
        base_url_env_var="OPENROUTER_BASE_URL",
    ),
    "nous": VoidcubeOverlay(
        auth_type="oauth_device_code",
        base_url_override="https://inference-api.nousresearch.com/v1",
    ),
    "qwen-oauth": VoidcubeOverlay(
        auth_type="oauth_external",
        base_url_override="https://portal.qwen.ai/v1",
        base_url_env_var="VOIDCUBE_QWEN_BASE_URL",
    ),
    "copilot-acp": VoidcubeOverlay(
        auth_type="external_process",
        base_url_override="acp://copilot",
        base_url_env_var="COPILOT_ACP_BASE_URL",
    ),
    "zai": VoidcubeOverlay(
        extra_env_vars=("GLM_API_KEY", "ZAI_API_KEY", "Z_AI_API_KEY"),
        base_url_env_var="GLM_BASE_URL",
    ),
    "kimi-coding": VoidcubeOverlay(
        base_url_env_var="KIMI_BASE_URL",
    ),
    "minimax": VoidcubeOverlay(
        base_url_env_var="MINIMAX_BASE_URL",
    ),
    "minimax-cn": VoidcubeOverlay(
        base_url_env_var="MINIMAX_CN_BASE_URL",
    ),
    "deepseek": VoidcubeOverlay(
        base_url_env_var="DEEPSEEK_BASE_URL",
    ),
}


# -- Resolved provider -------------------------------------------------------
# The merged result of models.dev + overlay + user config.

@dataclass
class ProviderDef:
    """Complete provider definition — merged from all sources."""

    id: str
    name: str
    api_key_env_vars: Tuple[str, ...]     # all env vars to check for API key
    base_url: str = ""
    base_url_env_var: str = ""
    is_aggregator: bool = False
    auth_type: str = "api_key"
    doc: str = ""
    source: str = ""                      # "models.dev", "VoidCube", "user-config"


# -- Aliases ------------------------------------------------------------------
# Maps human-friendly / legacy names to canonical provider IDs.
# Uses models.dev IDs where possible.

ALIASES: Dict[str, str] = {
    # openrouter
    # zai
    "glm": "zai",
    "z-ai": "zai",
    "z.ai": "zai",
    "zhipu": "zai",

    # kimi-for-coding (models.dev ID)
    "kimi": "kimi-coding",
    "moonshot": "kimi-coding",

    # minimax-cn
    "minimax-china": "minimax-cn",
    "minimax_cn": "minimax-cn",



    "github-copilot-acp": "copilot-acp",

    # deepseek
    "deep-seek": "deepseek",

    # Local server aliases → virtual "local" concept (resolved via user config)
    "lmstudio": "lmstudio",
    "lm-studio": "lmstudio",
    "lm_studio": "lmstudio",
    "ollama": "ollama-cloud",
    "vllm": "local",
    "llamacpp": "local",
    "llama.cpp": "local",
    "llama-cpp": "local",
}


# -- Display labels -----------------------------------------------------------
# Built dynamically from models.dev + overlays.  Fallback for providers
# not in the catalog.

_LABEL_OVERRIDES: Dict[str, str] = {
    "nous": "Nous Portal",
    "copilot-acp": "GitHub Copilot ACP",
    "local": "Local endpoint",
}


# -- Helper functions ---------------------------------------------------------

def normalize_provider(name: str) -> str:
    """Resolve aliases and normalise casing to a canonical provider id.

    Returns the canonical id string.  Does *not* validate that the id
    corresponds to a known provider.
    """
    key = name.strip().lower()
    return ALIASES.get(key, key)


def get_provider(name: str) -> Optional[ProviderDef]:
    """Look up a provider by id or alias, merging all data sources.

    Resolution order:
      1. Voidcube overlays for project-specific providers such as Nous.
      2. models.dev catalog + Voidcube overlay
      3. User-defined providers from config (TODO: Phase 4)

    Returns a fully-resolved ProviderDef or None.
    """
    canonical = normalize_provider(name)
    if canonical not in RUNTIME_PROVIDER_IDS:
        return None

    # Try to get models.dev data
    try:
        from agent.models_dev import get_provider_info as _mdev_provider
        mdev_info = _mdev_provider(canonical)
    except Exception:
        mdev_info = None

    overlay = VOIDCUBE_OVERLAYS.get(canonical)

    if mdev_info is not None:
        # Merge models.dev + overlay
        is_agg = overlay.is_aggregator if overlay else False
        auth = overlay.auth_type if overlay else "api_key"
        base_url_env = overlay.base_url_env_var if overlay else ""
        base_url_override = overlay.base_url_override if overlay else ""

        # Combine env vars: models.dev env + VoidCube extra
        env_vars = list(mdev_info.env)
        if overlay and overlay.extra_env_vars:
            for ev in overlay.extra_env_vars:
                if ev not in env_vars:
                    env_vars.append(ev)

        return ProviderDef(
            id=canonical,
            name=mdev_info.name,
            api_key_env_vars=tuple(env_vars),
            base_url=base_url_override or mdev_info.api,
            base_url_env_var=base_url_env,
            is_aggregator=is_agg,
            auth_type=auth,
            doc=mdev_info.doc,
            source="models.dev",
        )

    if overlay is not None:
        # Voidcube-only provider (not in models.dev)
        return ProviderDef(
            id=canonical,
            name=_LABEL_OVERRIDES.get(canonical, canonical),
            api_key_env_vars=overlay.extra_env_vars,
            base_url=overlay.base_url_override,
            base_url_env_var=overlay.base_url_env_var,
            is_aggregator=overlay.is_aggregator,
            auth_type=overlay.auth_type,
            source="VoidCube",
        )

    return None


def get_label(provider_id: str) -> str:
    """Get a human-readable display name for a provider."""
    canonical = normalize_provider(provider_id)

    # Check label overrides first
    if canonical in _LABEL_OVERRIDES:
        return _LABEL_OVERRIDES[canonical]

    # Try models.dev
    pdef = get_provider(canonical)
    if pdef:
        return pdef.name

    return canonical




def is_aggregator(provider: str) -> bool:
    """Return True when the provider is a multi-model aggregator."""
    pdef = get_provider(provider)
    return pdef.is_aggregator if pdef else False


# -- Provider from user config ------------------------------------------------

def resolve_user_provider(name: str, user_config: Dict[str, Any]) -> Optional[ProviderDef]:
    """Resolve a provider from the user's config.yaml ``providers:`` section.

    Args:
        name: Provider name as given by the user.
        user_config: The ``providers:`` dict from config.yaml.

    Returns:
        ProviderDef if found, else None.
    """
    if not user_config or not isinstance(user_config, dict):
        return None

    entry = user_config.get(name)
    if not isinstance(entry, dict):
        return None

    # Extract fields
    display_name = entry.get("label", "") or entry.get("name", "") or name
    api_url = entry.get("base_url", "") or entry.get("api", "") or entry.get("url", "") or ""
    key_env = entry.get("api_key_env", "") or entry.get("key_env", "") or ""
    env_vars: List[str] = []
    if key_env:
        env_vars.append(key_env)

    return ProviderDef(
        id=name,
        name=display_name,
        api_key_env_vars=tuple(env_vars),
        base_url=api_url,
        base_url_env_var="",
        is_aggregator=False,
        auth_type="none" if str(entry.get("auth_mode") or "").strip().lower() == "none" else "api_key",
        source="user-config",
    )


def resolve_provider_full(
    name: str,
    user_providers: Optional[Dict[str, Any]] = None,
) -> Optional[ProviderDef]:
    """Full resolution chain: built-in → models.dev → user config.

    This is the main entry point for --provider flag resolution.

    Args:
        name: Provider name or alias.
        user_providers: The ``providers:`` dict from config.yaml (optional).

    Returns:
        ProviderDef if found, else None.
    """
    canonical = normalize_provider(name)

    # 1. Built-in (models.dev + overlays)
    pdef = get_provider(canonical)
    if pdef is not None:
        return pdef

    # 2. User-defined providers from config
    if user_providers:
        # Try canonical name
        user_pdef = resolve_user_provider(canonical, user_providers)
        if user_pdef is not None:
            return user_pdef
        # Try original name (in case alias didn't match)
        user_pdef = resolve_user_provider(name.strip().lower(), user_providers)
        if user_pdef is not None:
            return user_pdef

    return None

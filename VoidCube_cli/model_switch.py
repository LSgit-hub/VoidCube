"""Shared model-switching logic for CLI and gateway /model commands.

Both the CLI (cli.py) and gateway (gateway/run.py) /model handlers
share the same core pipeline:

  parse flags -> alias resolution -> provider resolution ->
  credential resolution -> normalize model name ->
  metadata lookup -> build result

This module ties together the foundation layers:

- ``agent.models_dev``            -- models.dev catalog, ModelInfo, ProviderInfo
- ``VoidCube_cli.providers``        -- canonical provider identity + overlays
- ``VoidCube_cli.model_normalize``  -- per-provider name formatting

Provider switching uses the ``--provider`` flag exclusively.
No colon-based ``provider:model`` syntax — colons are reserved for
OpenRouter variant suffixes (``:free``, ``:extended``, ``:fast``).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import List, NamedTuple

from VoidCube_cli.providers import (
    get_label,
    is_aggregator,
    resolve_provider_full,
)
from VoidCube_cli.model_normalize import (
    normalize_model_for_provider,
)
from agent.models_dev import (
    ModelCapabilities,
    ModelInfo,
    get_model_capabilities,
    get_model_info,
    list_provider_models,
    search_models_dev,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Non-agentic model warning
# ---------------------------------------------------------------------------

_VOIDCUBE_MODEL_WARNING = (
    "Voidcube 3 & 4 models are NOT agentic and are not designed "
    "for use with Voidcube Agent. They lack the tool-calling capabilities "
    "required for agent workflows. Consider using an agentic model instead "
    "(GPT, Gemini, DeepSeek, etc.)."
)


def _check_VoidCube_model_warning(model_name: str) -> str:
    """Return a warning string if *model_name* looks like a Voidcube LLM model."""
    if "VoidCube" in model_name.lower():
        return _VOIDCUBE_MODEL_WARNING
    return ""


# ---------------------------------------------------------------------------
# Model aliases -- short names -> (vendor, family) with NO version numbers.
# Resolved dynamically against the live models.dev catalog.
# ---------------------------------------------------------------------------

class ModelVendor(NamedTuple):
    """Vendor slug and family prefix used for catalog resolution."""
    vendor: str
    family: str


# ---------------------------------------------------------------------------
# Direct aliases — exact model+provider+base_url for endpoints that aren't
# in the models.dev catalog (e.g. Ollama Cloud, local servers).
# Checked BEFORE catalog resolution.  Format:
#   alias -> (model_id, provider, base_url)
# These can also be loaded from config.yaml ``model_aliases:`` section.
# ---------------------------------------------------------------------------

class DirectAlias(NamedTuple):
    """Exact model mapping that bypasses catalog resolution."""
    model: str
    provider: str
    base_url: str


# ---------------------------------------------------------------------------
# Result dataclasses
# ---------------------------------------------------------------------------

@dataclass
class ModelSwitchResult:
    """Result of a model switch attempt."""

    success: bool
    new_model: str = ""
    target_provider: str = ""
    provider_changed: bool = False
    api_key: str = ""
    base_url: str = ""
    error_message: str = ""
    warning_message: str = ""
    provider_label: str = ""
    resolved_via_alias: str = ""
    capabilities: Optional[ModelCapabilities] = None
    model_info: Optional[ModelInfo] = None
    is_global: bool = False


@dataclass
class CustomAutoResult:
    """Result of switching to bare 'custom' provider with auto-detect."""

    success: bool
    model: str = ""
    base_url: str = ""
    api_key: str = ""
    error_message: str = ""


# ---------------------------------------------------------------------------
# Flag parsing
# ---------------------------------------------------------------------------

def parse_model_flags(raw_args: str) -> tuple[str, str, bool]:
    """Parse --provider and --global flags from /model command args.

    Returns (model_input, explicit_provider, is_global).

    Note: Model switches are always persisted by default. Use --session-only for
          temporary changes that won't survive restart.

    Examples::

        "gpt5"                           -> ("gpt5", "", True)
        "gpt5 --session-only"            -> ("gpt5", "", False)
        "--provider my-ollama"           -> ("", "my-ollama", True)
    """
    is_global = True
    explicit_provider = ""

    # Extract --session-only or --global
    if "--session-only" in raw_args:
        is_global = False
        raw_args = raw_args.replace("--session-only", "").strip()
    elif "--global" in raw_args:
        # Keep --global for backward compatibility, it's the default now
        is_global = True
        raw_args = raw_args.replace("--global", "").strip()

    # Extract --provider <name>
    parts = raw_args.split()
    i = 0
    filtered: list[str] = []
    while i < len(parts):
        if parts[i] == "--provider" and i + 1 < len(parts):
            explicit_provider = parts[i + 1]
            i += 2
        else:
            filtered.append(parts[i])
            i += 1

    model_input = " ".join(filtered).strip()
    return (model_input, explicit_provider, is_global)


# ---------------------------------------------------------------------------
# Core model-switching pipeline
# ---------------------------------------------------------------------------

def switch_model(
    raw_input: str,
    current_provider: str,
    current_model: str,
    current_base_url: str = "",
    current_api_key: str = "",
    is_global: bool = False,
    explicit_provider: str = "",
    user_providers: dict = None,
) -> ModelSwitchResult:
    """Core model-switching pipeline shared between CLI and gateway.

    Resolution chain:

      If --provider given:
        a. Resolve provider via resolve_provider_full()
        b. Resolve credentials
        c. If model given, resolve alias on target provider or use as-is
        d. If no model, auto-detect from endpoint

      If no --provider:
        a. Try alias resolution on current provider
        b. If alias exists but not on current provider -> fallback
        c. On aggregator, try vendor/model slug conversion
        d. Aggregator catalog search
        e. detect_provider_for_model() as last resort
        f. Resolve credentials
        g. Normalize model name for target provider

      Finally:
        h. Get full model metadata from models.dev
        i. Build result

    Args:
        raw_input: The model name (after flag parsing).
        current_provider: The currently active provider.
        current_model: The currently active model name.
        current_base_url: The currently active base URL.
        current_api_key: The currently active API key.
        is_global: Whether to persist the switch.
        explicit_provider: From --provider flag (empty = no explicit provider).
        user_providers: The ``providers:`` dict from config.yaml.

    Returns:
        ModelSwitchResult with all information the caller needs.
    """
    from VoidCube_app.models import (
        detect_provider_for_model,
        validate_requested_model,
    )
    from VoidCube_app.runtime_provider import resolve_runtime_provider

    new_model = raw_input.strip()
    target_provider = current_provider

    # =================================================================
    # PATH A: Explicit --provider given
    # =================================================================
    if explicit_provider:
        configured_provider_keys = set(user_providers.keys()) if isinstance(user_providers, dict) else set()
        # Resolve the provider
        pdef = resolve_provider_full(
            explicit_provider,
            user_providers,
        )
        if pdef is None:
            _switch_err = (
                f"Unknown provider '{explicit_provider}'. "
                f"Check 'VoidCube model' for available providers, or define it "
                f"in config.yaml under 'providers:'."
            )
            # Check for common config issues that cause provider resolution failures
            try:
                from VoidCube_app.config import validate_config_structure
                _cfg_issues = validate_config_structure()
                if _cfg_issues:
                    _switch_err += "\n\nRun 'VoidCube doctor' — config issues detected:"
                    for _ci in _cfg_issues[:3]:
                        _switch_err += f"\n  • {_ci.message}"
            except Exception:
                pass
            return ModelSwitchResult(
                success=False,
                is_global=is_global,
                error_message=_switch_err,
            )

        target_provider = pdef.id
        if configured_provider_keys and target_provider not in configured_provider_keys:
            return ModelSwitchResult(
                success=False,
                is_global=is_global,
                error_message=(
                    f"Provider '{explicit_provider}' is not configured. "
                    "Run /api first, then use /model to switch among configured providers."
                ),
            )

        # If no model specified, try auto-detect from endpoint
        if not new_model:
            if pdef.base_url:
                from VoidCube_app.runtime_provider import _auto_detect_local_model
                detected = _auto_detect_local_model(pdef.base_url)
                if detected:
                    new_model = detected
                else:
                    return ModelSwitchResult(
                        success=False,
                        target_provider=target_provider,
                        provider_label=pdef.name,
                        is_global=is_global,
                        error_message=(
                            f"No model detected on {pdef.name} ({pdef.base_url}). "
                            f"Specify the model explicitly: /model <model-name> --provider {explicit_provider}"
                        ),
                    )
            else:
                return ModelSwitchResult(
                    success=False,
                    target_provider=target_provider,
                    provider_label=pdef.name,
                    is_global=is_global,
                    error_message=(
                        f"Provider '{pdef.name}' has no base URL configured. "
                        f"Specify a model: /model <model-name> --provider {explicit_provider}"
                    ),
                )

    # =================================================================
    # PATH B: No explicit provider — resolve from model input
    # =================================================================
    else:
        if not target_provider:
            return ModelSwitchResult(
                success=False,
                is_global=is_global,
                error_message=(
                    "No active provider configured. Run /api first, "
                    "or use /model --provider <provider> after configuring one."
                ),
            )
        # --- Step a: On aggregator, convert vendor:model to vendor/model ---
        # Only convert when there's no slash — a slash means the name
        # is already in vendor/model format and the colon is a variant
        # tag (:free, :extended, :fast) that must be preserved.
        colon_pos = raw_input.find(":")
        if colon_pos > 0 and "/" not in raw_input and is_aggregator(current_provider):
            left = raw_input[:colon_pos].strip().lower()
            right = raw_input[colon_pos + 1:].strip()
            if left and right:
                # Colons become slashes for aggregator slugs
                new_model = f"{left}/{right}"
                logger.debug(
                    "Converted vendor:model '%s' to aggregator slug '%s'",
                    raw_input, new_model,
                )

        # --- Step b: Aggregator catalog search ---
        if is_aggregator(target_provider):
            catalog = list_provider_models(target_provider)
            if catalog:
                new_model_lower = new_model.lower()
                for mid in catalog:
                    if mid.lower() == new_model_lower:
                        new_model = mid
                        break
                else:
                    for mid in catalog:
                        if "/" in mid:
                            _, bare = mid.split("/", 1)
                            if bare.lower() == new_model_lower:
                                new_model = mid
                                break

        # --- Step c: detect_provider_for_model() as last resort ---
        _base = current_base_url or ""
        is_custom = current_provider in ("custom", "local") or (
            "localhost" in _base or "127.0.0.1" in _base
        )

        if (
            target_provider == current_provider
            and not is_custom
        ):
            detected = detect_provider_for_model(new_model, current_provider)
            if detected:
                target_provider, new_model = detected

    # =================================================================
    # COMMON PATH: Resolve credentials, normalize, get metadata
    # =================================================================

    provider_changed = target_provider != current_provider
    provider_label = get_label(target_provider)
    resolved_target = resolve_provider_full(target_provider, user_providers)
    if resolved_target is not None:
        provider_label = resolved_target.name

    # --- Resolve credentials ---
    api_key = current_api_key
    base_url = current_base_url
    if provider_changed or explicit_provider:
        try:
            runtime = resolve_runtime_provider(requested=target_provider)
            api_key = runtime.get("api_key", "")
            base_url = runtime.get("base_url", "")
        except Exception as e:
            return ModelSwitchResult(
                success=False,
                target_provider=target_provider,
                provider_label=provider_label,
                is_global=is_global,
                error_message=(
                    f"Could not resolve credentials for provider "
                    f"'{provider_label}': {e}"
                ),
            )
    else:
        try:
            runtime = resolve_runtime_provider(requested=current_provider)
            api_key = runtime.get("api_key", "")
            base_url = runtime.get("base_url", "")
        except Exception:
            pass

    # --- Normalize model name for target provider ---
    new_model = normalize_model_for_provider(new_model, target_provider)

    # --- Validate ---
    try:
        validation = validate_requested_model(
            new_model,
            target_provider,
            api_key=api_key,
            base_url=base_url,
        )
    except Exception:
        validation = {
            "accepted": True,
            "persist": True,
            "recognized": False,
            "message": None,
        }

    if not validation.get("accepted"):
        msg = validation.get("message", "Invalid model")
        return ModelSwitchResult(
            success=False,
            new_model=new_model,
            target_provider=target_provider,
            provider_label=provider_label,
            is_global=is_global,
            error_message=msg,
        )

    # --- Get capabilities (legacy) ---
    capabilities = get_model_capabilities(target_provider, new_model)

    # --- Get full model info from models.dev ---
    model_info = get_model_info(target_provider, new_model)

    # --- Collect warnings ---
    warnings: list[str] = []
    if validation.get("message"):
        warnings.append(validation["message"])
    VoidCube_warn = _check_VoidCube_model_warning(new_model)
    if VoidCube_warn:
        warnings.append(VoidCube_warn)

    # --- Build result ---
    return ModelSwitchResult(
        success=True,
        new_model=new_model,
        target_provider=target_provider,
        provider_changed=provider_changed,
        api_key=api_key,
        base_url=base_url,
        warning_message=" | ".join(warnings) if warnings else "",
        provider_label=provider_label,
        capabilities=capabilities,
        model_info=model_info,
        is_global=is_global,
    )


def list_configured_providers(
    current_provider: str = "",
    user_providers: dict = None,
    max_models: int = 8,
) -> List[dict]:
    """List providers explicitly configured in config.yaml."""
    from VoidCube_app.models import curated_models_for_provider

    results: List[dict] = []
    if not isinstance(user_providers, dict):
        return results

    for provider_key, provider_cfg in user_providers.items():
        if not isinstance(provider_cfg, dict):
            continue

        display_name = (
            provider_cfg.get("label")
            or provider_cfg.get("name")
            or provider_key
        )
        selected_model = str(
            provider_cfg.get("selected_model")
            or provider_cfg.get("default_model")
            or provider_cfg.get("model")
            or ""
        ).strip()

        models: list[str] = []
        try:
            curated = curated_models_for_provider(provider_key)
            models = [mid for mid, _ in curated[:max_models]]
        except Exception:
            models = []

        if selected_model:
            if selected_model in models:
                models.remove(selected_model)
            models.insert(0, selected_model)
        if max_models > 0:
            models = models[:max_models]

        results.append({
            "slug": provider_key,
            "name": display_name,
            "is_current": provider_key == current_provider,
            "is_user_defined": True,
            "models": models,
            "total_models": len(models),
            "source": "config",
            "api_url": provider_cfg.get("base_url", ""),
        })

    results.sort(key=lambda r: (not r["is_current"], r["name"].lower()))
    return results

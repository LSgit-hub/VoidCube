"""Model-switching logic for the CLI ``/model`` command.

``/model`` only selects a different model **within the active provider**.
Provider changes are owned by the ``/api`` wizard, never by this module.

The pipeline is:

  parse flags -> aggregator slug normalisation -> credential resolution ->
  model normalisation -> validation -> metadata lookup -> build result

This module ties together the foundation layers:

- ``voidcube.infrastructure.providers.models_dev`` -- models.dev catalog
- ``voidcube.interfaces.cli.providers``            -- provider identity
- ``voidcube.infrastructure.providers.model_normalization`` -- provider formatting
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import List, Optional

from .providers import (
    get_label,
    is_aggregator,
    resolve_provider_full,
)
from ...infrastructure.providers.model_normalization import (
    normalize_model_for_provider,
)
from ...infrastructure.providers.models_dev import (
    ModelCapabilities,
    ModelInfo,
    get_model_capabilities,
    get_model_info,
    list_provider_models,
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
# Result dataclasses
# ---------------------------------------------------------------------------

@dataclass
class ModelSwitchResult:
    """Result of a model switch attempt."""

    success: bool
    new_model: str = ""
    target_provider: str = ""
    api_key: str = ""
    base_url: str = ""
    error_message: str = ""
    warning_message: str = ""
    provider_label: str = ""
    capabilities: Optional[ModelCapabilities] = None
    model_info: Optional[ModelInfo] = None
    is_global: bool = False
    native_modalities: tuple[str, ...] | None = None


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

def parse_model_flags(raw_args: str) -> tuple[str, bool]:
    """Parse the persistence flag from /model command args.

    Returns (model_input, is_global). Model switches are always persisted by
    default; ``--session-only`` makes the change temporary.

    ``--provider`` is intentionally NOT parsed here: providers are changed
    through the /api wizard, and the handler rejects the flag explicitly.

    Examples::

        "gpt5"                -> ("gpt5", True)
        "gpt5 --session-only" -> ("gpt5", False)
    """
    is_global = True

    # Extract --session-only or --global
    if "--session-only" in raw_args:
        is_global = False
        raw_args = raw_args.replace("--session-only", "").strip()
    elif "--global" in raw_args:
        # Explicitly select the default persistent scope.
        is_global = True
        raw_args = raw_args.replace("--global", "").strip()

    return raw_args.strip(), is_global


# ---------------------------------------------------------------------------
# Core model-switching pipeline
# ---------------------------------------------------------------------------

def switch_model(
    raw_input: str,
    current_provider: str,
    current_base_url: str = "",
    current_api_key: str = "",
    is_global: bool = False,
    user_providers: dict = None,
) -> ModelSwitchResult:
    """Resolve a model switch within the currently active provider.

    The active provider is fixed: ``/model`` selects a different model that the
    current provider already exposes. Provider changes go through the ``/api``
    wizard and can never be triggered here.

    Resolution chain:

      a. On an aggregator, convert vendor:model to the vendor/model slug.
      b. On an aggregator, match the input against the provider catalog.
      c. Resolve credentials for the active provider.
      d. Normalise the model name for the provider.
      e. Validate, then attach models.dev metadata.

    Args:
        raw_input: The model name (after flag parsing).
        current_provider: The currently active provider.
        current_base_url: The currently active base URL.
        current_api_key: The currently active API key.
        is_global: Whether to persist the switch.
        user_providers: The ``providers:`` dict from config.yaml.

    Returns:
        ModelSwitchResult with all information the caller needs.
    """
    from ...infrastructure.providers.model_catalog import validate_requested_model
    from ...infrastructure.providers.runtime import resolve_runtime_provider

    new_model = raw_input.strip()
    target_provider = current_provider

    if not target_provider:
        return ModelSwitchResult(
            success=False,
            is_global=is_global,
            error_message=(
                "No active provider configured. Run /api to configure one."
            ),
        )

    # --- Step a: On aggregator, convert vendor:model to vendor/model ---
    # Only convert when there's no slash — a slash means the name is already
    # in vendor/model format and the colon is a variant tag (:free,
    # :extended, :fast) that must be preserved.
    colon_pos = raw_input.find(":")
    if colon_pos > 0 and "/" not in raw_input and is_aggregator(target_provider):
        left = raw_input[:colon_pos].strip().lower()
        right = raw_input[colon_pos + 1:].strip()
        if left and right:
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

    # --- Provider label ---
    provider_label = get_label(target_provider)
    resolved_target = resolve_provider_full(target_provider, user_providers)
    if resolved_target is not None:
        provider_label = resolved_target.name

    # --- Step c: Resolve credentials for the active provider ---
    api_key = current_api_key
    base_url = current_base_url
    try:
        runtime = resolve_runtime_provider(requested=current_provider)
        api_key = runtime.get("api_key", "") or api_key
        base_url = runtime.get("base_url", "") or base_url
    except Exception:
        pass

    # --- Step d: Normalize model name for the provider ---
    new_model = normalize_model_for_provider(new_model, target_provider)

    # --- Step e: Validate ---
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

    # --- Get model capabilities ---
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
    """List providers and models from the shared configured Provider pool.

    The picker must use the catalog persisted by ``/api``.  It intentionally
    does not query a second model registry here: explicit model overrides and
    models omitted by a provider's ``/models`` response must remain selectable.
    """
    from ...infrastructure.config.provider_config import provider_model_catalog

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
        model_override = str(provider_cfg.get("model_override") or "").strip()

        models: list[str] = []
        for model_id in (
            model_override,
            selected_model,
            *provider_model_catalog(provider_cfg),
        ):
            if model_id and model_id not in models:
                models.append(model_id)
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


def list_current_provider_models(
    current_provider: str,
    user_providers: dict = None,
    max_models: int = 0,
) -> List[str]:
    """Return the model IDs available for the currently active provider.

    Only the active provider's own catalog is returned so ``/model`` can never
    select a model that belongs to a different provider.
    """
    from ...infrastructure.config.provider_config import provider_model_catalog

    if not current_provider or not isinstance(user_providers, dict):
        return []

    provider_cfg = user_providers.get(current_provider)
    if not isinstance(provider_cfg, dict):
        return []

    models: list[str] = []
    for model_id in (
        str(provider_cfg.get("model_override") or "").strip(),
        str(provider_cfg.get("selected_model") or "").strip(),
        *provider_model_catalog(provider_cfg),
    ):
        if model_id and model_id not in models:
            models.append(model_id)
    if max_models > 0:
        models = models[:max_models]
    return models

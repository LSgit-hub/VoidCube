"""Dependency-injected orchestration for one auxiliary LLM call target."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Mapping


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
    kwargs: dict[str, Any]


def resolve_call_target(
    *,
    task: str | None,
    provider: str | None,
    model: str | None,
    base_url: str | None,
    api_key: str | None,
    main_runtime: Mapping[str, Any] | None,
    async_mode: bool,
    resolve_task_provider_model: Callable[..., tuple[str, str | None, str | None, str | None]],
    require_active_integration: Callable[..., None],
    resolve_vision_provider_client: Callable[..., tuple[str | None, Any, str | None]],
    get_cached_client: Callable[..., tuple[Any, str | None]],
    infer_active_provider: Callable[..., str],
    missing_provider_error: Callable[..., RuntimeError],
    log: Any,
) -> AuxiliaryCallTarget:
    requested_provider, resolved_model, resolved_base_url, resolved_api_key = resolve_task_provider_model(
        task, provider, model, base_url, api_key
    )
    require_active_integration(requested_provider, resolved_model, resolved_base_url)

    if task == "vision":
        active_provider, client, final_model = resolve_vision_provider_client(
            provider=provider,
            model=model,
            base_url=base_url,
            api_key=api_key,
            async_mode=async_mode,
        )
        if client is None:
            raise missing_provider_error(task, requested_provider)
        active_provider = active_provider or requested_provider
    else:
        client, final_model = get_cached_client(
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
                    f"Provider '{explicit}' is set in config.yaml but no API key was found. "
                    f"Set the {explicit.upper()}_API_KEY environment variable, or switch "
                    "to a different provider with `/model`."
                )
            if requested_provider == "auto":
                log.info(
                    "Auxiliary %s: provider %s unavailable, trying auto-detection chain",
                    task or "call",
                    requested_provider,
                )
                client, final_model = get_cached_client(
                    "auto",
                    resolved_model,
                    async_mode=async_mode,
                    main_runtime=main_runtime,
                )
        if client is None:
            raise missing_provider_error(task, requested_provider)
        active_provider = infer_active_provider(requested_provider, client, main_runtime)

    final_model = str(final_model or resolved_model or "").strip()
    if not final_model:
        raise RuntimeError(f"No LLM model resolved for task={task} provider={active_provider}")
    effective_base_url = str(getattr(client, "base_url", "") or resolved_base_url or "")
    return AuxiliaryCallTarget(
        requested_provider=requested_provider,
        active_provider=active_provider,
        model=final_model,
        base_url=effective_base_url,
        client=client,
    )


__all__ = ["AuxiliaryCallTarget", "AuxiliaryFallbackCall", "resolve_call_target"]

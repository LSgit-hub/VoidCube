"""Dependency-injected vision client construction policy."""

from __future__ import annotations

from typing import Any, Callable, Iterable


def resolve_vision_client(
    *,
    requested: str,
    resolved_model: str | None,
    resolved_base_url: str | None,
    resolved_api_key: str | None,
    async_mode: bool,
    main_provider: str,
    main_model: str,
    provider_order: Iterable[str],
    strict_backend: Callable[[str], tuple[Any, str | None]],
    resolve_provider_client: Callable[..., tuple[Any, str | None]],
    get_cached_client: Callable[..., tuple[Any, str | None]],
    to_async_client: Callable[[Any, str | None], tuple[Any, str | None]],
    log: Any,
) -> tuple[str | None, Any, str | None]:
    """Resolve the concrete client for a vision request.

    Provider construction remains injected from the compatibility adapter;
    this module owns only the stable selection order and final async shape.
    """
    def finalize(provider: str, sync_client: Any, default_model: str | None):
        if sync_client is None:
            return provider, None, None
        final_model = resolved_model or default_model
        if async_mode:
            client, model = to_async_client(sync_client, final_model)
            return provider, client, model
        return provider, sync_client, final_model

    if resolved_base_url:
        client, final_model = resolve_provider_client(
            "custom",
            model=resolved_model,
            async_mode=async_mode,
            explicit_base_url=resolved_base_url,
            explicit_api_key=resolved_api_key,
        )
        return ("custom", client, final_model) if client is not None else ("custom", None, None)

    order = tuple(provider_order)
    if requested == "auto":
        if main_provider and main_provider not in ("auto", ""):
            if main_provider in order:
                client, default_model = strict_backend(main_provider)
                if client is not None:
                    return finalize(main_provider, client, default_model)
            else:
                client, model = resolve_provider_client(main_provider, main_model)
                if client is not None:
                    log.info(
                        "Vision auto-detect: using active provider %s (%s)",
                        main_provider,
                        model or main_model,
                    )
                    return finalize(main_provider, client, model or main_model)
        for candidate in order:
            if candidate == main_provider:
                continue
            client, default_model = strict_backend(candidate)
            if client is not None:
                return finalize(candidate, client, default_model)
        log.debug("Auxiliary vision client: none available")
        return None, None, None

    if requested in order:
        client, default_model = strict_backend(requested)
        return finalize(requested, client, default_model)

    client, final_model = get_cached_client(requested, resolved_model, async_mode)
    if client is None:
        return requested, None, None
    return requested, client, final_model


__all__ = ["resolve_vision_client"]

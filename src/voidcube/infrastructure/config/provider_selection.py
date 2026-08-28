"""Persist the active Provider/model selection through the canonical config service."""

from __future__ import annotations

from collections.abc import Iterable

from .configuration import (
    load_config,
    save_config,
    set_active_provider,
    set_provider_model,
)


def persist_provider_selection(
    provider: str,
    model: str,
    native_modalities: Iterable[str] | None = None,
) -> None:
    """Make one Provider/model pair active in the persisted configuration."""
    config = load_config()
    if native_modalities is None:
        config = set_provider_model(config, provider, model, make_active=True)
        config = set_active_provider(config, provider)
    else:
        from .provider_config import persist_api_a_selection

        config = persist_api_a_selection(
            config,
            provider=provider,
            model=model,
            native_modalities=native_modalities,
        )
    save_config(config)


__all__ = ["persist_provider_selection"]

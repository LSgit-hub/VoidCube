"""Persist the active Provider/model selection through the canonical config service."""

from __future__ import annotations

from .configuration import (
    load_config,
    save_config,
    set_active_provider,
    set_provider_model,
)


def persist_provider_selection(provider: str, model: str) -> None:
    """Make one Provider/model pair active in the persisted configuration."""
    config = load_config()
    config = set_provider_model(config, provider, model, make_active=True)
    config = set_active_provider(config, provider)
    save_config(config)


__all__ = ["persist_provider_selection"]

"""CLI compatibility export for shared model identifier handling."""

from __future__ import annotations

from VoidCube_app.model_normalization import (
    AGGREGATOR_PROVIDERS,
    normalize_model_for_provider,
)

_AGGREGATOR_PROVIDERS = AGGREGATOR_PROVIDERS

__all__ = ["_AGGREGATOR_PROVIDERS", "normalize_model_for_provider"]

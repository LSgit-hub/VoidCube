"""Compatibility facade for canonical provider infrastructure."""

try:
    from voidcube.infrastructure.providers import (
        PROVIDER_REGISTRY,
        RUNTIME_PROVIDER_IDS,
        SPECIAL_RUNTIME_PROVIDER_IDS,
        ProviderConfig,
    )
except ModuleNotFoundError:
    from src.voidcube.infrastructure.providers import (
        PROVIDER_REGISTRY,
        RUNTIME_PROVIDER_IDS,
        SPECIAL_RUNTIME_PROVIDER_IDS,
        ProviderConfig,
    )

__all__ = [
    "PROVIDER_REGISTRY",
    "RUNTIME_PROVIDER_IDS",
    "SPECIAL_RUNTIME_PROVIDER_IDS",
    "ProviderConfig",
]

"""Compatibility facade for the canonical application clock."""

try:
    from voidcube.infrastructure.shared.clock import get_timezone, now, reset_cache
except (ModuleNotFoundError, ImportError):
    from src.voidcube.infrastructure.shared.clock import get_timezone, now, reset_cache

__all__ = ["get_timezone", "now", "reset_cache"]

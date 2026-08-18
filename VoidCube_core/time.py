"""Compatibility facade for the canonical application clock."""

from VoidCube_app.infrastructure.shared.clock import get_timezone, now, reset_cache

__all__ = ["get_timezone", "now", "reset_cache"]

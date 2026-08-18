"""Compatibility facade for canonical execution environments."""

try:
    from voidcube.infrastructure.execution.environments import BaseEnvironment
except (ModuleNotFoundError, ImportError):
    from src.voidcube.infrastructure.execution.environments import BaseEnvironment

__all__ = ["BaseEnvironment"]

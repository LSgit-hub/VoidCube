"""Compatibility facade for the canonical execution system."""

try:
    from voidcube.systems.execution import *
except (ModuleNotFoundError, ImportError):
    from src.voidcube.systems.execution import *

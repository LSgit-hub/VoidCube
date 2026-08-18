"""Compatibility facade for canonical research knowledge."""

try:
    from voidcube.systems.research_knowledge import *
except (ModuleNotFoundError, ImportError):
    from src.voidcube.systems.research_knowledge import *


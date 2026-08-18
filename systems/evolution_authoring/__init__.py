"""Compatibility facade for canonical evolution authoring."""

try:
    from voidcube.systems.evolution_authoring import *
except (ModuleNotFoundError, ImportError):
    from src.voidcube.systems.evolution_authoring import *


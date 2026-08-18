"""Compatibility facade for canonical evolution evaluation."""

try:
    from voidcube.systems.evolution_evaluation import *
except (ModuleNotFoundError, ImportError):
    from src.voidcube.systems.evolution_evaluation import *


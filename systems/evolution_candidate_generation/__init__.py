"""Compatibility facade for canonical evolution candidate generation."""

try:
    from voidcube.systems.evolution_candidate_generation import *
except (ModuleNotFoundError, ImportError):
    from src.voidcube.systems.evolution_candidate_generation import *


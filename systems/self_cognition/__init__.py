"""Compatibility facade for canonical self cognition."""

try:
    from voidcube.systems.self_cognition import *
except (ModuleNotFoundError, ImportError):
    from src.voidcube.systems.self_cognition import *


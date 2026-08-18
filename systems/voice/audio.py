"""Compatibility alias for canonical voice audio adapters."""
import sys
try:
    from voidcube.systems.voice import audio as _implementation
except (ModuleNotFoundError, ImportError):
    from src.voidcube.systems.voice import audio as _implementation
sys.modules[__name__] = _implementation

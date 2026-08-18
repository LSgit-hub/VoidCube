"""Compatibility alias for canonical voice activity detector."""
import sys
try:
    from voidcube.systems.voice import vad as _implementation
except (ModuleNotFoundError, ImportError):
    from src.voidcube.systems.voice import vad as _implementation
sys.modules[__name__] = _implementation

"""Compatibility alias for canonical text-to-speech adapter."""
import sys
try:
    from voidcube.systems.voice import tts as _implementation
except (ModuleNotFoundError, ImportError):
    from src.voidcube.systems.voice import tts as _implementation
sys.modules[__name__] = _implementation

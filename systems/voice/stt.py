"""Compatibility alias for canonical speech-to-text adapter."""
import sys
try:
    from voidcube.systems.voice import stt as _implementation
except (ModuleNotFoundError, ImportError):
    from src.voidcube.systems.voice import stt as _implementation
sys.modules[__name__] = _implementation

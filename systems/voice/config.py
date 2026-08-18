"""Compatibility alias for canonical voice configuration."""
import sys
try:
    from voidcube.systems.voice import config as _implementation
except (ModuleNotFoundError, ImportError):
    from src.voidcube.systems.voice import config as _implementation
sys.modules[__name__] = _implementation

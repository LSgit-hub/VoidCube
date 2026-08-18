"""Compatibility alias for canonical voice session runtime."""
import sys
try:
    from voidcube.systems.voice import session as _implementation
except (ModuleNotFoundError, ImportError):
    from src.voidcube.systems.voice import session as _implementation
sys.modules[__name__] = _implementation

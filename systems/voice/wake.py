"""Compatibility alias for canonical wake-word adapter."""
import sys
try:
    from voidcube.systems.voice import wake as _implementation
except (ModuleNotFoundError, ImportError):
    from src.voidcube.systems.voice import wake as _implementation
sys.modules[__name__] = _implementation

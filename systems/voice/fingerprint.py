"""Compatibility alias for canonical voice fingerprint adapters."""
import sys
try:
    from voidcube.systems.voice import fingerprint as _implementation
except (ModuleNotFoundError, ImportError):
    from src.voidcube.systems.voice import fingerprint as _implementation
sys.modules[__name__] = _implementation

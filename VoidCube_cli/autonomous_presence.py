"""Compatibility alias for canonical autonomous presence adapter."""
import sys
try:
    from voidcube.interfaces.cli.autonomous import presence as _implementation
except (ModuleNotFoundError, ImportError):
    from src.voidcube.interfaces.cli.autonomous import presence as _implementation
sys.modules[__name__] = _implementation

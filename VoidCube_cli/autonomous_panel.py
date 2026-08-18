"""Compatibility alias for canonical autonomous panel presentation."""
import sys
try:
    from voidcube.interfaces.cli.autonomous import panel as _implementation
except (ModuleNotFoundError, ImportError):
    from src.voidcube.interfaces.cli.autonomous import panel as _implementation
sys.modules[__name__] = _implementation

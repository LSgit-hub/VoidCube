"""Compatibility alias for canonical turn scheduler adapter."""
import sys
try:
    from voidcube.interfaces.cli.turn import scheduler as _implementation
except (ModuleNotFoundError, ImportError):
    from src.voidcube.interfaces.cli.turn import scheduler as _implementation
sys.modules[__name__] = _implementation

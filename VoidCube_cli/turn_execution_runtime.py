"""Compatibility alias for canonical turn execution adapter."""
import sys
try:
    from voidcube.interfaces.cli.turn import execution as _implementation
except (ModuleNotFoundError, ImportError):
    from src.voidcube.interfaces.cli.turn import execution as _implementation
sys.modules[__name__] = _implementation

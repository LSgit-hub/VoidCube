"""Compatibility alias for canonical turn postprocessing adapter."""
import sys
try:
    from voidcube.interfaces.cli.turn import postprocessing as _implementation
except (ModuleNotFoundError, ImportError):
    from src.voidcube.interfaces.cli.turn import postprocessing as _implementation
sys.modules[__name__] = _implementation

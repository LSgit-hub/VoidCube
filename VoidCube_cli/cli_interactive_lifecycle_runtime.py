"""Compatibility alias for canonical CLI lifecycle runtime."""
import sys
try:
    from voidcube.interfaces.cli.lifecycle import runtime as _implementation
except (ModuleNotFoundError, ImportError):
    from src.voidcube.interfaces.cli.lifecycle import runtime as _implementation
sys.modules[__name__] = _implementation

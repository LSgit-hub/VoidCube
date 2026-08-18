"""Compatibility alias for canonical CLI interactive state runtime."""
import sys
try:
    from voidcube.interfaces.cli.lifecycle import state as _implementation
except (ModuleNotFoundError, ImportError):
    from src.voidcube.interfaces.cli.lifecycle import state as _implementation
sys.modules[__name__] = _implementation

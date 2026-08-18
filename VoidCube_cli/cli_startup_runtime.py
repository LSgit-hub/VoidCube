"""Compatibility alias for canonical CLI startup runtime."""
import sys
try:
    from voidcube.interfaces.cli.lifecycle import startup as _implementation
except (ModuleNotFoundError, ImportError):
    from src.voidcube.interfaces.cli.lifecycle import startup as _implementation
sys.modules[__name__] = _implementation

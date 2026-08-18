"""Compatibility alias for canonical CLI lifecycle assembly."""
import sys
try:
    from voidcube.interfaces.cli.lifecycle import assembly as _implementation
except (ModuleNotFoundError, ImportError):
    from src.voidcube.interfaces.cli.lifecycle import assembly as _implementation
sys.modules[__name__] = _implementation

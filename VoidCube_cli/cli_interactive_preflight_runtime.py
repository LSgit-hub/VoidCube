"""Compatibility alias for canonical CLI preflight runtime."""
import sys
try:
    from voidcube.interfaces.cli.lifecycle import preflight as _implementation
except (ModuleNotFoundError, ImportError):
    from src.voidcube.interfaces.cli.lifecycle import preflight as _implementation
sys.modules[__name__] = _implementation

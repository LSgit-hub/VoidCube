"""Compatibility alias for canonical CLI registration runtime."""
import sys
try:
    from voidcube.interfaces.cli.lifecycle import registration as _implementation
except (ModuleNotFoundError, ImportError):
    from src.voidcube.interfaces.cli.lifecycle import registration as _implementation
sys.modules[__name__] = _implementation

"""Compatibility alias for canonical autonomous event adapters."""
import sys
try:
    from voidcube.interfaces.cli.autonomous import events as _implementation
except (ModuleNotFoundError, ImportError):
    from src.voidcube.interfaces.cli.autonomous import events as _implementation
sys.modules[__name__] = _implementation

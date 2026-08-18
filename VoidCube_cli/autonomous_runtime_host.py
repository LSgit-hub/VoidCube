"""Compatibility alias for canonical autonomous runtime host."""
import sys
try:
    from voidcube.interfaces.cli.autonomous import runtime_host as _implementation
except (ModuleNotFoundError, ImportError):
    from src.voidcube.interfaces.cli.autonomous import runtime_host as _implementation
sys.modules[__name__] = _implementation

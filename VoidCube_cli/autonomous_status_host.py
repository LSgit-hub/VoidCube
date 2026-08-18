"""Compatibility alias for canonical autonomous status host."""
import sys
try:
    from voidcube.interfaces.cli.autonomous import status_host as _implementation
except (ModuleNotFoundError, ImportError):
    from src.voidcube.interfaces.cli.autonomous import status_host as _implementation
sys.modules[__name__] = _implementation

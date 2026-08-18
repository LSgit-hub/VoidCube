"""Compatibility alias for canonical autonomous observation adapters."""
import sys
try:
    from voidcube.interfaces.cli.autonomous import observation as _implementation
except (ModuleNotFoundError, ImportError):
    from src.voidcube.interfaces.cli.autonomous import observation as _implementation
sys.modules[__name__] = _implementation

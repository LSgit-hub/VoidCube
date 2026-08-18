"""Compatibility alias for canonical autonomous execution output."""
import sys
try:
    from voidcube.interfaces.cli.autonomous import execution_output as _implementation
except (ModuleNotFoundError, ImportError):
    from src.voidcube.interfaces.cli.autonomous import execution_output as _implementation
sys.modules[__name__] = _implementation

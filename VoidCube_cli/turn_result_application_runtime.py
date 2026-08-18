"""Compatibility alias for canonical turn result adapter."""
import sys
try:
    from voidcube.interfaces.cli.turn import result_application as _implementation
except (ModuleNotFoundError, ImportError):
    from src.voidcube.interfaces.cli.turn import result_application as _implementation
sys.modules[__name__] = _implementation

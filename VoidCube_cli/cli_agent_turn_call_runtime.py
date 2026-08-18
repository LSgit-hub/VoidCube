"""Compatibility alias for canonical agent turn call adapter."""
import sys
try:
    from voidcube.interfaces.cli.turn import agent_call as _implementation
except (ModuleNotFoundError, ImportError):
    from src.voidcube.interfaces.cli.turn import agent_call as _implementation
sys.modules[__name__] = _implementation

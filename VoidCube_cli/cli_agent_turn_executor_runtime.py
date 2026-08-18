"""Compatibility alias for canonical agent turn executor adapter."""
import sys
try:
    from voidcube.interfaces.cli.turn import agent_executor as _implementation
except (ModuleNotFoundError, ImportError):
    from src.voidcube.interfaces.cli.turn import agent_executor as _implementation
sys.modules[__name__] = _implementation

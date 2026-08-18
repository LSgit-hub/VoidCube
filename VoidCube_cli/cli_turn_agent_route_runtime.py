"""Compatibility alias for canonical turn-agent route runtime."""

import sys

try:
    from voidcube.interfaces.cli import turn_agent_route_runtime as _implementation
except (ModuleNotFoundError, ImportError):
    from src.voidcube.interfaces.cli import turn_agent_route_runtime as _implementation

sys.modules[__name__] = _implementation

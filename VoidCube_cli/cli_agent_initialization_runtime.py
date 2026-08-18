"""Compatibility alias for canonical agent initialization runtime."""

try:
    from voidcube.interfaces.cli.agent_initialization_runtime import *  # noqa: F401,F403
except ModuleNotFoundError:
    from src.voidcube.interfaces.cli.agent_initialization_runtime import *  # noqa: F401,F403

"""Compatibility alias for canonical CLI agent executor runtime."""

try:
    from voidcube.interfaces.cli.turn.agent_executor_runtime import *  # noqa: F401,F403
except ModuleNotFoundError:
    from src.voidcube.interfaces.cli.turn.agent_executor_runtime import *  # noqa: F401,F403

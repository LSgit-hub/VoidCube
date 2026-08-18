"""Compatibility alias for canonical dynamic command runtime."""

try:
    from voidcube.interfaces.cli.dynamic_command_runtime import *  # noqa: F401,F403
except ModuleNotFoundError:
    from src.voidcube.interfaces.cli.dynamic_command_runtime import *  # noqa: F401,F403

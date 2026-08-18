"""Compatibility alias for canonical command availability runtime."""

try:
    from voidcube.interfaces.cli.command_availability_runtime import *  # noqa: F401,F403
except ModuleNotFoundError:
    from src.voidcube.interfaces.cli.command_availability_runtime import *  # noqa: F401,F403

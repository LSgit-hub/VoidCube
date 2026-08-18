"""Compatibility alias for canonical session lifecycle runtime."""

try:
    from voidcube.interfaces.cli.session_lifecycle import *  # noqa: F401,F403
except ModuleNotFoundError:
    from src.voidcube.interfaces.cli.session_lifecycle import *  # noqa: F401,F403

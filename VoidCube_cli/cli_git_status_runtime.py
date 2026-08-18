"""Compatibility alias for canonical git status runtime."""

try:
    from voidcube.interfaces.cli.git_status_runtime import *  # noqa: F401,F403
except ModuleNotFoundError:
    from src.voidcube.interfaces.cli.git_status_runtime import *  # noqa: F401,F403

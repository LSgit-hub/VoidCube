"""Compatibility alias for canonical status bar runtime."""

try:
    from voidcube.interfaces.cli.status_bar_runtime import *  # noqa: F401,F403
except ModuleNotFoundError:
    from src.voidcube.interfaces.cli.status_bar_runtime import *  # noqa: F401,F403

"""Compatibility alias for the canonical TUI application composition."""

try:
    from voidcube.interfaces.cli.tui.application import *  # noqa: F401,F403
except ModuleNotFoundError:
    from src.voidcube.interfaces.cli.tui.application import *  # noqa: F401,F403

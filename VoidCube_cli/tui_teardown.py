"""Compatibility alias for canonical TUI teardown runtime."""

try:
    from voidcube.interfaces.cli.tui.teardown import *  # noqa: F401,F403
except ModuleNotFoundError:
    from src.voidcube.interfaces.cli.tui.teardown import *  # noqa: F401,F403

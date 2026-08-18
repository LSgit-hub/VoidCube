"""Compatibility alias for canonical dynamic TUI text runtime."""

try:
    from voidcube.interfaces.cli.tui.dynamic_text_runtime import *  # noqa: F401,F403
except ModuleNotFoundError:
    from src.voidcube.interfaces.cli.tui.dynamic_text_runtime import *  # noqa: F401,F403

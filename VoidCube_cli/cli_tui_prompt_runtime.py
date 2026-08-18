"""Compatibility alias for canonical TUI prompt runtime."""

try:
    from voidcube.interfaces.cli.tui.prompt_runtime import *  # noqa: F401,F403
except ModuleNotFoundError:
    from src.voidcube.interfaces.cli.tui.prompt_runtime import *  # noqa: F401,F403

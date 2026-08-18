"""Compatibility alias for canonical TUI layout metrics runtime."""

try:
    from voidcube.interfaces.cli.tui.layout_metrics_runtime import *  # noqa: F401,F403
except ModuleNotFoundError:
    from src.voidcube.interfaces.cli.tui.layout_metrics_runtime import *  # noqa: F401,F403

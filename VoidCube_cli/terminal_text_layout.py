"""Compatibility alias for canonical terminal layout helpers."""

try:
    from voidcube.interfaces.cli.terminal_text_layout import *  # noqa: F401,F403
except ModuleNotFoundError:
    from src.voidcube.interfaces.cli.terminal_text_layout import *  # noqa: F401,F403

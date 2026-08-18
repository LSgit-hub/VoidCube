"""Compatibility alias for canonical voice keybinding runtime."""

try:
    from voidcube.interfaces.cli.voice_keybinding_runtime import *  # noqa: F401,F403
except ModuleNotFoundError:
    from src.voidcube.interfaces.cli.voice_keybinding_runtime import *  # noqa: F401,F403

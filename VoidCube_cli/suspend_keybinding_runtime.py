"""Compatibility alias for canonical suspend keybinding runtime."""

try:
    from voidcube.interfaces.cli.suspend_keybinding_runtime import *  # noqa: F401,F403
except ModuleNotFoundError:
    from src.voidcube.interfaces.cli.suspend_keybinding_runtime import *  # noqa: F401,F403

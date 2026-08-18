"""Compatibility alias for canonical Enter keybinding runtime."""

try:
    from voidcube.interfaces.cli.enter_keybinding_runtime import *  # noqa: F401,F403
except ModuleNotFoundError:
    from src.voidcube.interfaces.cli.enter_keybinding_runtime import *  # noqa: F401,F403

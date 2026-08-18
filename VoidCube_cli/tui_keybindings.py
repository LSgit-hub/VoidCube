"""Compatibility alias for canonical TUI runtime."""

import sys

try:
    from voidcube.interfaces.cli.tui import keybindings as _implementation
except (ModuleNotFoundError, ImportError):
    from src.voidcube.interfaces.cli.tui import keybindings as _implementation

sys.modules[__name__] = _implementation

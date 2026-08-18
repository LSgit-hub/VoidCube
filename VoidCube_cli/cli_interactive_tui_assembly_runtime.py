"""Compatibility module alias for canonical CLI TUI composition."""

import sys

try:
    from voidcube.interfaces.cli.tui import composition as _implementation
except (ModuleNotFoundError, ImportError):
    from src.voidcube.interfaces.cli.tui import composition as _implementation

sys.modules[__name__] = _implementation

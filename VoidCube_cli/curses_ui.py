"""Compatibility alias for canonical curses UI helpers."""

import sys

try:
    from voidcube.interfaces.cli import curses_ui as _implementation
except (ModuleNotFoundError, ImportError):
    from src.voidcube.interfaces.cli import curses_ui as _implementation

sys.modules[__name__] = _implementation

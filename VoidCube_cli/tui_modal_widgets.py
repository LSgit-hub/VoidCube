"""Compatibility alias for canonical TUI runtime."""

import sys

try:
    from voidcube.interfaces.cli.tui import modal_widgets as _implementation
except (ModuleNotFoundError, ImportError):
    from src.voidcube.interfaces.cli.tui import modal_widgets as _implementation

sys.modules[__name__] = _implementation

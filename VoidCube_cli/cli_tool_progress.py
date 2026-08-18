"""Compatibility module alias for canonical CLI cli tool progress."""

import sys

try:
    from voidcube.interfaces.cli import cli_tool_progress as _implementation
except (ModuleNotFoundError, ImportError):
    from src.voidcube.interfaces.cli import cli_tool_progress as _implementation

sys.modules[__name__] = _implementation

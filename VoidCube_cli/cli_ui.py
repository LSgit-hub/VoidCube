"""Compatibility module alias for canonical CLI cli ui."""

import sys

try:
    from voidcube.interfaces.cli import cli_ui as _implementation
except (ModuleNotFoundError, ImportError):
    from src.voidcube.interfaces.cli import cli_ui as _implementation

sys.modules[__name__] = _implementation

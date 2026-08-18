"""Compatibility module alias for canonical CLI output helpers."""

import sys

try:
    from voidcube.interfaces.cli import cli_output as _implementation
except (ModuleNotFoundError, ImportError):
    from src.voidcube.interfaces.cli import cli_output as _implementation

sys.modules[__name__] = _implementation

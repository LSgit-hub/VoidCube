"""Compatibility module alias for canonical CLI command execution."""

import sys

try:
    from voidcube.interfaces.cli.commands import execution as _implementation
except (ModuleNotFoundError, ImportError):
    from src.voidcube.interfaces.cli.commands import execution as _implementation

sys.modules[__name__] = _implementation

"""Compatibility alias for canonical CLI command handler personality."""

import sys

try:
    from voidcube.interfaces.cli.commands.handlers import personality as _implementation
except (ModuleNotFoundError, ImportError):
    from src.voidcube.interfaces.cli.commands.handlers import personality as _implementation

sys.modules[__name__] = _implementation

"""Compatibility alias for canonical CLI command handler plan."""

import sys

try:
    from voidcube.interfaces.cli.commands.handlers import plan as _implementation
except (ModuleNotFoundError, ImportError):
    from src.voidcube.interfaces.cli.commands.handlers import plan as _implementation

sys.modules[__name__] = _implementation

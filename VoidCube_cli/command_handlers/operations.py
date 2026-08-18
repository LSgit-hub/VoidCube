"""Compatibility alias for canonical CLI command handler operations."""

import sys

try:
    from voidcube.interfaces.cli.commands.handlers import operations as _implementation
except (ModuleNotFoundError, ImportError):
    from src.voidcube.interfaces.cli.commands.handlers import operations as _implementation

sys.modules[__name__] = _implementation

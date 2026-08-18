"""Compatibility alias for canonical CLI command handler voice."""

import sys

try:
    from voidcube.interfaces.cli.commands.handlers import voice as _implementation
except (ModuleNotFoundError, ImportError):
    from src.voidcube.interfaces.cli.commands.handlers import voice as _implementation

sys.modules[__name__] = _implementation

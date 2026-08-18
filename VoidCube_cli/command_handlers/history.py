"""Compatibility alias for canonical CLI command handler history."""

import sys

try:
    from voidcube.interfaces.cli.commands.handlers import history as _implementation
except (ModuleNotFoundError, ImportError):
    from src.voidcube.interfaces.cli.commands.handlers import history as _implementation

sys.modules[__name__] = _implementation

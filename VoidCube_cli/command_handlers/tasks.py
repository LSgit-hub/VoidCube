"""Compatibility alias for canonical CLI command handler tasks."""

import sys

try:
    from voidcube.interfaces.cli.commands.handlers import tasks as _implementation
except (ModuleNotFoundError, ImportError):
    from src.voidcube.interfaces.cli.commands.handlers import tasks as _implementation

sys.modules[__name__] = _implementation

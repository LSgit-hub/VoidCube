"""Compatibility alias for canonical CLI command handler autonomous."""

import sys

try:
    from voidcube.interfaces.cli.commands.handlers import autonomous as _implementation
except (ModuleNotFoundError, ImportError):
    from src.voidcube.interfaces.cli.commands.handlers import autonomous as _implementation

sys.modules[__name__] = _implementation

"""Compatibility alias for canonical CLI command handler tools."""

import sys

try:
    from voidcube.interfaces.cli.commands.handlers import tools as _implementation
except (ModuleNotFoundError, ImportError):
    from src.voidcube.interfaces.cli.commands.handlers import tools as _implementation

sys.modules[__name__] = _implementation

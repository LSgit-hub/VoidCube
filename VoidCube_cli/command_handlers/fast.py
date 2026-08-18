"""Compatibility alias for canonical CLI command handler fast."""

import sys

try:
    from voidcube.interfaces.cli.commands.handlers import fast as _implementation
except (ModuleNotFoundError, ImportError):
    from src.voidcube.interfaces.cli.commands.handlers import fast as _implementation

sys.modules[__name__] = _implementation

"""Compatibility alias for canonical CLI command handler compression."""

import sys

try:
    from voidcube.interfaces.cli.commands.handlers import compression as _implementation
except (ModuleNotFoundError, ImportError):
    from src.voidcube.interfaces.cli.commands.handlers import compression as _implementation

sys.modules[__name__] = _implementation

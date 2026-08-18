"""Compatibility alias for canonical CLI command handler mcp."""

import sys

try:
    from voidcube.interfaces.cli.commands.handlers import mcp as _implementation
except (ModuleNotFoundError, ImportError):
    from src.voidcube.interfaces.cli.commands.handlers import mcp as _implementation

sys.modules[__name__] = _implementation

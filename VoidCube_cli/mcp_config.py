"""Compatibility module alias for canonical CLI MCP configuration."""

import sys

try:
    from voidcube.interfaces.cli import mcp_config as _implementation
except (ModuleNotFoundError, ImportError):
    from src.voidcube.interfaces.cli import mcp_config as _implementation

sys.modules[__name__] = _implementation

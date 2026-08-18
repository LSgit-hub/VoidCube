"""Compatibility alias for canonical MCP OAuth support."""

import sys

try:
    from voidcube.extensions.tools.mcp import oauth as _implementation
except (ModuleNotFoundError, ImportError):
    from src.voidcube.extensions.tools.mcp import oauth as _implementation

sys.modules[__name__] = _implementation

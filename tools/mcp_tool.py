"""Compatibility alias for the canonical MCP client tool."""

import sys

try:
    from voidcube.extensions.tools.mcp import mcp_tool as _implementation
except (ModuleNotFoundError, ImportError):
    from src.voidcube.extensions.tools.mcp import mcp_tool as _implementation

sys.modules[__name__] = _implementation

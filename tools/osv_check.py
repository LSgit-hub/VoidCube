"""Compatibility alias for canonical MCP package malware checks."""

import sys

try:
    from voidcube.extensions.tools import osv_check as _implementation
except (ModuleNotFoundError, ImportError):
    from src.voidcube.extensions.tools import osv_check as _implementation

sys.modules[__name__] = _implementation

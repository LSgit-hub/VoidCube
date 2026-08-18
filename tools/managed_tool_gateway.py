"""Compatibility alias for canonical managed gateway adapter."""

import sys

try:
    from voidcube.infrastructure.gateway import managed_tool_gateway as _implementation
except (ModuleNotFoundError, ImportError):
    from src.voidcube.infrastructure.gateway import managed_tool_gateway as _implementation

sys.modules[__name__] = _implementation

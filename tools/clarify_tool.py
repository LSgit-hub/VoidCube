"""Compatibility alias for canonical clarification tool."""

import sys

try:
    from voidcube.extensions.tools import clarify_tool as _implementation
except (ModuleNotFoundError, ImportError):
    from src.voidcube.extensions.tools import clarify_tool as _implementation

sys.modules[__name__] = _implementation

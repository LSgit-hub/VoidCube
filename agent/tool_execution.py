"""Compatibility alias for the canonical agent tool execution runtime."""

import sys

try:
    from voidcube.runtime.agent import tool_execution as _implementation
except (ModuleNotFoundError, ImportError):
    from src.voidcube.runtime.agent import tool_execution as _implementation

sys.modules[__name__] = _implementation

"""Compatibility alias for the canonical terminal execution backend."""

import sys

try:
    from voidcube.infrastructure.execution import terminal_tool as _implementation
except (ModuleNotFoundError, ImportError):
    from src.voidcube.infrastructure.execution import terminal_tool as _implementation

sys.modules[__name__] = _implementation

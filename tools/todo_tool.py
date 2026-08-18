"""Compatibility alias for canonical todo tool."""

import sys

try:
    from voidcube.extensions.tools import todo_tool as _implementation
except (ModuleNotFoundError, ImportError):
    from src.voidcube.extensions.tools import todo_tool as _implementation

sys.modules[__name__] = _implementation

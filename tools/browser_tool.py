"""Compatibility alias for canonical browser tool definitions."""

import sys

try:
    from voidcube.extensions.tools.browser import browser_tool as _implementation
except (ModuleNotFoundError, ImportError):
    from src.voidcube.extensions.tools.browser import browser_tool as _implementation

sys.modules[__name__] = _implementation

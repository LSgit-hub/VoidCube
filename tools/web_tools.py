"""Compatibility alias for canonical web tools."""

import sys

try:
    from voidcube.extensions.tools.web import web_tools as _implementation
except (ModuleNotFoundError, ImportError):
    from src.voidcube.extensions.tools.web import web_tools as _implementation

sys.modules[__name__] = _implementation

"""Compatibility alias for canonical local web tools."""

import sys

try:
    from voidcube.extensions.tools.web import web_tools_local as _implementation
except (ModuleNotFoundError, ImportError):
    from src.voidcube.extensions.tools.web import web_tools_local as _implementation

sys.modules[__name__] = _implementation

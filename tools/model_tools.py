"""Compatibility alias for canonical tool discovery and dispatch."""

import sys

try:
    from voidcube.extensions.tools import model_tools as _implementation
except (ModuleNotFoundError, ImportError):
    from src.voidcube.extensions.tools import model_tools as _implementation

sys.modules[__name__] = _implementation

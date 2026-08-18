"""Compatibility alias for canonical operations tools."""

import sys

try:
    from voidcube.extensions.tools import ops_register as _implementation
except (ModuleNotFoundError, ImportError):
    from src.voidcube.extensions.tools import ops_register as _implementation

sys.modules[__name__] = _implementation

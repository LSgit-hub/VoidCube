"""Compatibility alias for canonical extension debug helpers."""

import sys

try:
    from voidcube.extensions.tools import debug_helpers as _implementation
except (ModuleNotFoundError, ImportError):
    from src.voidcube.extensions.tools import debug_helpers as _implementation

sys.modules[__name__] = _implementation

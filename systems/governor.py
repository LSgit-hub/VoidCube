"""Compatibility alias for canonical governor."""

import sys

try:
    from voidcube.systems import governor as _implementation
except (ModuleNotFoundError, ImportError):
    from src.voidcube.systems import governor as _implementation

sys.modules[__name__] = _implementation


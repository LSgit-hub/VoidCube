"""Compatibility alias for canonical body probe runtime."""

import sys

try:
    from voidcube.systems import probe as _implementation
except (ModuleNotFoundError, ImportError):
    from src.voidcube.systems import probe as _implementation

sys.modules[__name__] = _implementation


"""Compatibility alias for canonical body lifecycle."""

import sys

try:
    from voidcube.systems import lifecycle as _implementation
except (ModuleNotFoundError, ImportError):
    from src.voidcube.systems import lifecycle as _implementation

sys.modules[__name__] = _implementation


"""Compatibility alias for canonical model routing policy."""

import sys

try:
    from voidcube.application import model_routing as _implementation
except (ModuleNotFoundError, ImportError):
    from src.voidcube.application import model_routing as _implementation

sys.modules[__name__] = _implementation


"""Compatibility alias for canonical body registry."""

import sys

try:
    from voidcube.systems import body_registry as _implementation
except (ModuleNotFoundError, ImportError):
    from src.voidcube.systems import body_registry as _implementation

sys.modules[__name__] = _implementation


"""Compatibility alias for canonical execution route hints."""

import sys

try:
    from voidcube.systems.execution import route_hints as _implementation
except (ModuleNotFoundError, ImportError):
    from src.voidcube.systems.execution import route_hints as _implementation

sys.modules[__name__] = _implementation


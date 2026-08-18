"""Compatibility alias for canonical execution service."""

import sys

try:
    from voidcube.systems.execution import service as _implementation
except (ModuleNotFoundError, ImportError):
    from src.voidcube.systems.execution import service as _implementation

sys.modules[__name__] = _implementation


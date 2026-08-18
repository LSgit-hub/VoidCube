"""Compatibility alias for canonical execution adapters."""

import sys

try:
    from voidcube.systems.execution import adapters as _implementation
except (ModuleNotFoundError, ImportError):
    from src.voidcube.systems.execution import adapters as _implementation

sys.modules[__name__] = _implementation


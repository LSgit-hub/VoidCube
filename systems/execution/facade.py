"""Compatibility alias for canonical execution facade."""

import sys

try:
    from voidcube.systems.execution import facade as _implementation
except (ModuleNotFoundError, ImportError):
    from src.voidcube.systems.execution import facade as _implementation

sys.modules[__name__] = _implementation


"""Compatibility alias for execution interruption state."""

import sys

try:
    from voidcube.infrastructure.execution import interrupt as _implementation
except (ModuleNotFoundError, ImportError):
    from src.voidcube.infrastructure.execution import interrupt as _implementation

sys.modules[__name__] = _implementation

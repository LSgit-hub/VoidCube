"""Compatibility alias for canonical turn input routing contracts."""

import sys

try:
    from voidcube.domain.contracts import turn_queue as _implementation
except (ModuleNotFoundError, ImportError):
    from src.voidcube.domain.contracts import turn_queue as _implementation

sys.modules[__name__] = _implementation

"""Compatibility alias for canonical domain contract turn."""

import sys

try:
    from voidcube.domain.contracts import turn as _implementation
except (ModuleNotFoundError, ImportError):
    from src.voidcube.domain.contracts import turn as _implementation

sys.modules[__name__] = _implementation

"""Compatibility alias for canonical domain contract executor."""

import sys

try:
    from voidcube.domain.contracts import executor as _implementation
except (ModuleNotFoundError, ImportError):
    from src.voidcube.domain.contracts import executor as _implementation

sys.modules[__name__] = _implementation

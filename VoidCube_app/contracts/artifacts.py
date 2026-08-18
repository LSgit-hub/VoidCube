"""Compatibility alias for canonical domain contract artifacts."""

import sys

try:
    from voidcube.domain.contracts import artifacts as _implementation
except (ModuleNotFoundError, ImportError):
    from src.voidcube.domain.contracts import artifacts as _implementation

sys.modules[__name__] = _implementation

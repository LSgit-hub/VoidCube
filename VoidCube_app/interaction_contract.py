"""Compatibility alias for canonical domain contract interaction."""

import sys

try:
    from voidcube.domain.contracts import interaction as _implementation
except (ModuleNotFoundError, ImportError):
    from src.voidcube.domain.contracts import interaction as _implementation

sys.modules[__name__] = _implementation

"""Compatibility alias for canonical domain contract ports."""

import sys

try:
    from voidcube.domain.contracts import ports as _implementation
except (ModuleNotFoundError, ImportError):
    from src.voidcube.domain.contracts import ports as _implementation

sys.modules[__name__] = _implementation

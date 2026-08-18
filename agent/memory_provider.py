"""Compatibility alias for the canonical memory provider port."""

import sys

try:
    from voidcube.domain.contracts import memory as _implementation
except (ModuleNotFoundError, ImportError):
    from src.voidcube.domain.contracts import memory as _implementation

sys.modules[__name__] = _implementation

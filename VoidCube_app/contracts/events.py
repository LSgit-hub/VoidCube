"""Compatibility alias for canonical domain contract events."""

import sys

try:
    from voidcube.domain.contracts import events as _implementation
except (ModuleNotFoundError, ImportError):
    from src.voidcube.domain.contracts import events as _implementation

sys.modules[__name__] = _implementation

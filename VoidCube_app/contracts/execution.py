"""Compatibility alias for canonical domain contract execution."""

import sys

try:
    from voidcube.domain.contracts import execution as _implementation
except (ModuleNotFoundError, ImportError):
    from src.voidcube.domain.contracts import execution as _implementation

sys.modules[__name__] = _implementation

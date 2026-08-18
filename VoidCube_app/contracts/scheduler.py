"""Compatibility alias for canonical scheduler contracts."""

import sys

try:
    from voidcube.domain.contracts import scheduler as _implementation
except (ModuleNotFoundError, ImportError):
    from src.voidcube.domain.contracts import scheduler as _implementation

sys.modules[__name__] = _implementation

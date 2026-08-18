"""Compatibility alias for the canonical gateway executor client."""

import sys

try:
    from voidcube.infrastructure.gateway import executor as _implementation
except (ModuleNotFoundError, ImportError):
    from src.voidcube.infrastructure.gateway import executor as _implementation

sys.modules[__name__] = _implementation

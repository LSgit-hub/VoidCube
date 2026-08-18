"""Compatibility alias for canonical skill management operations."""

import sys

try:
    from voidcube.extensions.skills import manager as _implementation
except (ModuleNotFoundError, ImportError):
    from src.voidcube.extensions.skills import manager as _implementation

sys.modules[__name__] = _implementation

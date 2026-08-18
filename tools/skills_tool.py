"""Compatibility alias for canonical skill tool operations."""

import sys

try:
    from voidcube.extensions.skills import tool as _implementation
except (ModuleNotFoundError, ImportError):
    from src.voidcube.extensions.skills import tool as _implementation

sys.modules[__name__] = _implementation

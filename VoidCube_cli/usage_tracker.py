"""Compatibility alias for canonical usage tracking."""

import sys

try:
    from voidcube.infrastructure.providers import usage_tracker as _implementation
except (ModuleNotFoundError, ImportError):
    from src.voidcube.infrastructure.providers import usage_tracker as _implementation

sys.modules[__name__] = _implementation

"""Compatibility alias for canonical Provider rate-limit tracking."""

import sys

try:
    from voidcube.infrastructure.providers import rate_limit as _implementation
except (ModuleNotFoundError, ImportError):
    from src.voidcube.infrastructure.providers import rate_limit as _implementation

sys.modules[__name__] = _implementation

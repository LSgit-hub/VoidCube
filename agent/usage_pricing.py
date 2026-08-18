"""Compatibility alias for canonical Provider usage pricing."""

import sys

try:
    from voidcube.infrastructure.providers import usage_pricing as _implementation
except (ModuleNotFoundError, ImportError):
    from src.voidcube.infrastructure.providers import usage_pricing as _implementation

sys.modules[__name__] = _implementation

"""Compatibility alias for canonical Provider model metadata services."""

import sys

try:
    from voidcube.infrastructure.providers import model_metadata as _implementation
except (ModuleNotFoundError, ImportError):
    from src.voidcube.infrastructure.providers import model_metadata as _implementation

sys.modules[__name__] = _implementation

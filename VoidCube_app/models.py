"""Compatibility alias for canonical provider model catalog services."""

import sys

try:
    from voidcube.infrastructure.providers import model_catalog as _implementation
except (ModuleNotFoundError, ImportError):
    from src.voidcube.infrastructure.providers import model_catalog as _implementation

sys.modules[__name__] = _implementation

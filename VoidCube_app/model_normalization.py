"""Compatibility module alias for canonical provider model normalization."""

import sys

try:
    from voidcube.infrastructure.providers import model_normalization as _implementation
except (ModuleNotFoundError, ImportError):
    from src.voidcube.infrastructure.providers import model_normalization as _implementation

sys.modules[__name__] = _implementation

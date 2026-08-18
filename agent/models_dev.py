"""Compatibility alias for canonical provider model metadata."""

import sys

try:
    from voidcube.infrastructure.providers import models_dev as _implementation
except (ModuleNotFoundError, ImportError):
    from src.voidcube.infrastructure.providers import models_dev as _implementation

sys.modules[__name__] = _implementation


"""Compatibility alias for canonical SOUL configuration parsing."""

import sys

try:
    from voidcube.infrastructure.config import soul_config as _implementation
except (ModuleNotFoundError, ImportError):
    from src.voidcube.infrastructure.config import soul_config as _implementation

sys.modules[__name__] = _implementation

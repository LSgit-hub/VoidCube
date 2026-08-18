"""Compatibility alias for canonical system configuration."""

import sys

try:
    from voidcube.infrastructure.config import system as _implementation
except (ModuleNotFoundError, ImportError):
    from src.voidcube.infrastructure.config import system as _implementation

sys.modules[__name__] = _implementation


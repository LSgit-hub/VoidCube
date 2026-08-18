"""Compatibility alias for the canonical Mem host integration adapter."""

import sys

try:
    from voidcube.infrastructure.memory import host_integration as _implementation
except (ModuleNotFoundError, ImportError):
    from src.voidcube.infrastructure.memory import host_integration as _implementation

sys.modules[__name__] = _implementation

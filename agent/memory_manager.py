"""Compatibility alias for canonical application memory orchestration."""

import sys

try:
    from voidcube.application import memory_manager as _implementation
except (ModuleNotFoundError, ImportError):
    from src.voidcube.application import memory_manager as _implementation

sys.modules[__name__] = _implementation

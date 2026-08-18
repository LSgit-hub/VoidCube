"""Compatibility alias for the canonical Mem governor bridge."""

import sys

try:
    from voidcube.infrastructure.memory import governor_bridge as _implementation
except (ModuleNotFoundError, ImportError):
    from src.voidcube.infrastructure.memory import governor_bridge as _implementation

sys.modules[__name__] = _implementation

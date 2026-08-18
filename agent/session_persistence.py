"""Compatibility alias for canonical session transcript persistence."""

import sys

try:
    from voidcube.infrastructure.persistence import session_runtime as _implementation
except (ModuleNotFoundError, ImportError):
    from src.voidcube.infrastructure.persistence import session_runtime as _implementation

sys.modules[__name__] = _implementation

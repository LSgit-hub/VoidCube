"""Compatibility alias for canonical subdirectory hint tracking."""

import sys

try:
    from voidcube.runtime.agent import subdirectory_hints as _implementation
except (ModuleNotFoundError, ImportError):
    from src.voidcube.runtime.agent import subdirectory_hints as _implementation

sys.modules[__name__] = _implementation

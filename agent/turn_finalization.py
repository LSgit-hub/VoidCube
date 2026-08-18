"""Compatibility alias for canonical turn finalization."""

import sys

try:
    from voidcube.runtime.agent import turn_finalization as _implementation
except (ModuleNotFoundError, ImportError):
    from src.voidcube.runtime.agent import turn_finalization as _implementation

sys.modules[__name__] = _implementation

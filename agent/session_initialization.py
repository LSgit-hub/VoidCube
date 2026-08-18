"""Compatibility alias for canonical Agent session initialization."""

import sys

try:
    from voidcube.runtime.agent import session_initialization as _implementation
except (ModuleNotFoundError, ImportError):
    from src.voidcube.runtime.agent import session_initialization as _implementation

sys.modules[__name__] = _implementation

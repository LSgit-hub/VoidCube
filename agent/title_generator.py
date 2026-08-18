"""Compatibility alias for application session title generation."""

import sys

try:
    from voidcube.application import session_title as _implementation
except (ModuleNotFoundError, ImportError):
    from src.voidcube.application import session_title as _implementation

sys.modules[__name__] = _implementation

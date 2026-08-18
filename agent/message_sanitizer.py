"""Compatibility alias for canonical message sanitization."""

import sys

try:
    from voidcube.domain.agent import message_sanitizer as _implementation
except (ModuleNotFoundError, ImportError):
    from src.voidcube.domain.agent import message_sanitizer as _implementation

sys.modules[__name__] = _implementation

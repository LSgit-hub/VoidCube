"""Compatibility alias for canonical conversation turn runtime."""

import sys

try:
    from voidcube.domain.agent import conversation_runtime as _implementation
except (ModuleNotFoundError, ImportError):
    from src.voidcube.domain.agent import conversation_runtime as _implementation

sys.modules[__name__] = _implementation

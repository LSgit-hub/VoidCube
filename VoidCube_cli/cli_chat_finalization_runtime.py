"""Compatibility alias for canonical chat finalization runtime."""

import sys

try:
    from voidcube.interfaces.cli import chat_finalization_runtime as _implementation
except (ModuleNotFoundError, ImportError):
    from src.voidcube.interfaces.cli import chat_finalization_runtime as _implementation

sys.modules[__name__] = _implementation

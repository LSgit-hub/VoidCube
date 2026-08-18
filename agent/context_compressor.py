"""Compatibility alias for canonical Agent context compression runtime."""

import sys

try:
    from voidcube.runtime.agent import context_compressor as _implementation
except (ModuleNotFoundError, ImportError):
    from src.voidcube.runtime.agent import context_compressor as _implementation

sys.modules[__name__] = _implementation

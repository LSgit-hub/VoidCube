"""Compatibility alias for canonical compression feedback rules."""

import sys

try:
    from voidcube.domain.agent import manual_compression_feedback as _implementation
except (ModuleNotFoundError, ImportError):
    from src.voidcube.domain.agent import manual_compression_feedback as _implementation

sys.modules[__name__] = _implementation

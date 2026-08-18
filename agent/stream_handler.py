"""Compatibility alias for canonical Agent stream I/O handling."""

import sys

try:
    from voidcube.runtime.agent import stream_handler as _implementation
except (ModuleNotFoundError, ImportError):
    from src.voidcube.runtime.agent import stream_handler as _implementation

sys.modules[__name__] = _implementation

"""Compatibility alias for canonical streaming response assembly."""

import sys

try:
    from voidcube.infrastructure.llm import stream_response as _implementation
except (ModuleNotFoundError, ImportError):
    from src.voidcube.infrastructure.llm import stream_response as _implementation

sys.modules[__name__] = _implementation

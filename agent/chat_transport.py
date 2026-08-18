"""Compatibility alias for the canonical LLM transport runtime."""

import sys

try:
    from voidcube.infrastructure.llm import transport_runtime as _implementation
except (ModuleNotFoundError, ImportError):
    from src.voidcube.infrastructure.llm import transport_runtime as _implementation

sys.modules[__name__] = _implementation

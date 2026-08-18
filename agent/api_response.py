"""Compatibility alias for the canonical LLM response contract."""

import sys

try:
    from voidcube.infrastructure.llm import response as _implementation
except (ModuleNotFoundError, ImportError):
    from src.voidcube.infrastructure.llm import response as _implementation

sys.modules[__name__] = _implementation


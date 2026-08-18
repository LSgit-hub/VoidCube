"""Compatibility alias for canonical LLM retry policy."""

import sys

try:
    from voidcube.infrastructure.llm import retry_policy as _implementation
except (ModuleNotFoundError, ImportError):
    from src.voidcube.infrastructure.llm import retry_policy as _implementation

sys.modules[__name__] = _implementation

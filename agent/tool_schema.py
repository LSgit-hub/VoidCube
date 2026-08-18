"""Compatibility alias for canonical tool schema normalization."""

import sys

try:
    from voidcube.infrastructure.llm import tool_schema as _implementation
except (ModuleNotFoundError, ImportError):
    from src.voidcube.infrastructure.llm import tool_schema as _implementation

sys.modules[__name__] = _implementation


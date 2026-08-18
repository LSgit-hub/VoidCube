"""Compatibility facade for the canonical LLM request protocol."""

from __future__ import annotations

import sys

try:
    from voidcube.infrastructure.llm import request as _implementation
except (ModuleNotFoundError, ImportError):
    from src.voidcube.infrastructure.llm import request as _implementation

sys.modules[__name__] = _implementation
setattr(sys.modules[__package__], "api_request", _implementation)

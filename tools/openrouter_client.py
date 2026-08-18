"""Compatibility alias for canonical OpenRouter client helpers."""

import sys

try:
    from voidcube.infrastructure.providers import openrouter_client as _implementation
except (ModuleNotFoundError, ImportError):
    from src.voidcube.infrastructure.providers import openrouter_client as _implementation

sys.modules[__name__] = _implementation

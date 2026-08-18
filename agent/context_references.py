"""Compatibility alias for canonical context-reference rules."""

import sys

try:
    from voidcube.domain.agent import context_references as _implementation
except (ModuleNotFoundError, ImportError):
    from src.voidcube.domain.agent import context_references as _implementation

sys.modules[__name__] = _implementation

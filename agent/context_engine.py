"""Compatibility alias for the canonical context engine port."""

import sys

try:
    from voidcube.domain.agent import context_engine as _implementation
except (ModuleNotFoundError, ImportError):
    from src.voidcube.domain.agent import context_engine as _implementation

sys.modules[__name__] = _implementation

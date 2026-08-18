"""Compatibility alias for the canonical gateway agent adapter."""

import sys

try:
    from voidcube.infrastructure.gateway import agent_adapter as _implementation
except (ModuleNotFoundError, ImportError):
    from src.voidcube.infrastructure.gateway import agent_adapter as _implementation

sys.modules[__name__] = _implementation

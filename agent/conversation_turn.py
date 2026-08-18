"""Compatibility alias for canonical conversation turn state."""

import sys

try:
    from voidcube.domain.agent import conversation_turn as _implementation
except (ModuleNotFoundError, ImportError):
    from src.voidcube.domain.agent import conversation_turn as _implementation

sys.modules[__name__] = _implementation

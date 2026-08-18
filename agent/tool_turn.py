"""Compatibility alias for canonical tool-turn orchestration."""

import sys

try:
    from voidcube.runtime.agent import tool_turn as _implementation
except (ModuleNotFoundError, ImportError):
    from src.voidcube.runtime.agent import tool_turn as _implementation

sys.modules[__name__] = _implementation

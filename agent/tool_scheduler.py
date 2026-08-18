"""Compatibility alias for canonical tool scheduling policy."""

import sys

try:
    from voidcube.domain.agent import tool_scheduler as _implementation
except (ModuleNotFoundError, ImportError):
    from src.voidcube.domain.agent import tool_scheduler as _implementation

sys.modules[__name__] = _implementation

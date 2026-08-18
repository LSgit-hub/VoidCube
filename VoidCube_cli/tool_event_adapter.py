"""Compatibility module alias for canonical CLI tool event adapter."""

import sys

try:
    from voidcube.interfaces.cli import tool_event_adapter as _implementation
except (ModuleNotFoundError, ImportError):
    from src.voidcube.interfaces.cli import tool_event_adapter as _implementation

sys.modules[__name__] = _implementation

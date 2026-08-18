"""Compatibility module alias for canonical CLI session command adapters."""

import sys

try:
    from voidcube.interfaces.cli import session_command_adapter as _implementation
except (ModuleNotFoundError, ImportError):
    from src.voidcube.interfaces.cli import session_command_adapter as _implementation

sys.modules[__name__] = _implementation

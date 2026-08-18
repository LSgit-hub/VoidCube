"""Compatibility module alias for canonical CLI runtime handlers."""

import sys

try:
    from voidcube.interfaces.cli import runtime_handlers as _implementation
except (ModuleNotFoundError, ImportError):
    from src.voidcube.interfaces.cli import runtime_handlers as _implementation

sys.modules[__name__] = _implementation

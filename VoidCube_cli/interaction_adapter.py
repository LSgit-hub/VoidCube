"""Compatibility module alias for canonical CLI interaction adapter."""

import sys

try:
    from voidcube.interfaces.cli import interaction_adapter as _implementation
except (ModuleNotFoundError, ImportError):
    from src.voidcube.interfaces.cli import interaction_adapter as _implementation

sys.modules[__name__] = _implementation

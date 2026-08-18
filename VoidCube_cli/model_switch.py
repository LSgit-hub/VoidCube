"""Compatibility module alias for canonical CLI model switching."""

import sys

try:
    from voidcube.interfaces.cli import model_switch as _implementation
except (ModuleNotFoundError, ImportError):
    from src.voidcube.interfaces.cli import model_switch as _implementation

sys.modules[__name__] = _implementation

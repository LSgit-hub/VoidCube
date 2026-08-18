"""Compatibility module alias for canonical CLI provider definitions."""

import sys

try:
    from voidcube.interfaces.cli import providers as _implementation
except (ModuleNotFoundError, ImportError):
    from src.voidcube.interfaces.cli import providers as _implementation

sys.modules[__name__] = _implementation

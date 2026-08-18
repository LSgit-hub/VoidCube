"""Compatibility module alias for canonical CLI status presentation."""

import sys

try:
    from voidcube.interfaces.cli import status as _implementation
except (ModuleNotFoundError, ImportError):
    from src.voidcube.interfaces.cli import status as _implementation

sys.modules[__name__] = _implementation

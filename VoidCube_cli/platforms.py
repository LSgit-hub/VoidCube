"""Compatibility module alias for canonical CLI platform metadata."""

import sys

try:
    from voidcube.interfaces.cli import platforms as _implementation
except (ModuleNotFoundError, ImportError):
    from src.voidcube.interfaces.cli import platforms as _implementation

sys.modules[__name__] = _implementation

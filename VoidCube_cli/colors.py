"""Compatibility module alias for canonical CLI color utilities."""

import sys

try:
    from voidcube.interfaces.cli import colors as _implementation
except (ModuleNotFoundError, ImportError):
    from src.voidcube.interfaces.cli import colors as _implementation

sys.modules[__name__] = _implementation

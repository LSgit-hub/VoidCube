"""Compatibility alias for canonical CLI display primitives."""

import sys

try:
    from voidcube.interfaces.cli import display as _implementation
except (ModuleNotFoundError, ImportError):
    from src.voidcube.interfaces.cli import display as _implementation

sys.modules[__name__] = _implementation

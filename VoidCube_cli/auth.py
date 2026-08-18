"""Compatibility module alias for canonical CLI authentication commands."""

import sys

try:
    from voidcube.interfaces.cli import auth as _implementation
except (ModuleNotFoundError, ImportError):
    from src.voidcube.interfaces.cli import auth as _implementation

sys.modules[__name__] = _implementation

"""Compatibility module alias for canonical CLI banner."""

import sys

try:
    from voidcube.interfaces.cli import banner as _implementation
except (ModuleNotFoundError, ImportError):
    from src.voidcube.interfaces.cli import banner as _implementation

sys.modules[__name__] = _implementation

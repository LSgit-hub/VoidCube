"""Compatibility alias for canonical Windows console setup."""

import sys

try:
    from voidcube.interfaces.cli import console_fix as _implementation
except (ModuleNotFoundError, ImportError):
    from src.voidcube.interfaces.cli import console_fix as _implementation

sys.modules[__name__] = _implementation

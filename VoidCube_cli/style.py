"""Compatibility alias for canonical CLI style constants."""

import sys

try:
    from voidcube.interfaces.cli import style as _implementation
except (ModuleNotFoundError, ImportError):
    from src.voidcube.interfaces.cli import style as _implementation

sys.modules[__name__] = _implementation

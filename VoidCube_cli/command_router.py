"""Compatibility module alias for canonical CLI command routing."""

import sys

try:
    from voidcube.interfaces.cli.commands import router as _implementation
except (ModuleNotFoundError, ImportError):
    from src.voidcube.interfaces.cli.commands import router as _implementation

sys.modules[__name__] = _implementation

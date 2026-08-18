"""Compatibility module alias for canonical CLI provider presentation."""

import sys

try:
    from voidcube.interfaces.cli import provider_runtime as _implementation
except (ModuleNotFoundError, ImportError):
    from src.voidcube.interfaces.cli import provider_runtime as _implementation

sys.modules[__name__] = _implementation

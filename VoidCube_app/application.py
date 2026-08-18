"""Compatibility alias for canonical application runtime state."""

import sys

try:
    from voidcube.application import application_runtime as _implementation
except (ModuleNotFoundError, ImportError):
    from src.voidcube.application import application_runtime as _implementation

sys.modules[__name__] = _implementation

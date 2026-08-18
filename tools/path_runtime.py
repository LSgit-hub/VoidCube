"""Compatibility alias for execution path normalization."""

import sys

try:
    from voidcube.infrastructure.execution import path_runtime as _implementation
except (ModuleNotFoundError, ImportError):
    from src.voidcube.infrastructure.execution import path_runtime as _implementation

sys.modules[__name__] = _implementation

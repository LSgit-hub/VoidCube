"""Compatibility alias for canonical execution path utilities."""

import sys

try:
    from voidcube.infrastructure.execution import path_utils as _implementation
except (ModuleNotFoundError, ImportError):
    from src.voidcube.infrastructure.execution import path_utils as _implementation

sys.modules[__name__] = _implementation

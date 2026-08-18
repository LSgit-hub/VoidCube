"""Compatibility alias for canonical environment file synchronization."""

import sys

try:
    from voidcube.infrastructure.execution.environments import file_sync as _implementation
except (ModuleNotFoundError, ImportError):
    from src.voidcube.infrastructure.execution.environments import file_sync as _implementation

sys.modules[__name__] = _implementation

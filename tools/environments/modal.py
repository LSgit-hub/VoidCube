"""Compatibility alias for canonical Modal environment."""

import sys

try:
    from voidcube.infrastructure.execution.environments import modal as _implementation
except (ModuleNotFoundError, ImportError):
    from src.voidcube.infrastructure.execution.environments import modal as _implementation

sys.modules[__name__] = _implementation

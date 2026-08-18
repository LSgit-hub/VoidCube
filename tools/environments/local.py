"""Compatibility alias for the canonical local execution environment."""

import sys

try:
    from voidcube.infrastructure.execution.environments import local as _implementation
except (ModuleNotFoundError, ImportError):
    from src.voidcube.infrastructure.execution.environments import local as _implementation

sys.modules[__name__] = _implementation

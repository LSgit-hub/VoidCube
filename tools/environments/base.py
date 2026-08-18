"""Compatibility alias for the canonical execution environment base."""

import sys

try:
    from voidcube.infrastructure.execution.environments import base as _implementation
except (ModuleNotFoundError, ImportError):
    from src.voidcube.infrastructure.execution.environments import base as _implementation

sys.modules[__name__] = _implementation

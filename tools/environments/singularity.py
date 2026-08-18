"""Compatibility alias for canonical Singularity environment."""

import sys

try:
    from voidcube.infrastructure.execution.environments import singularity as _implementation
except (ModuleNotFoundError, ImportError):
    from src.voidcube.infrastructure.execution.environments import singularity as _implementation

sys.modules[__name__] = _implementation

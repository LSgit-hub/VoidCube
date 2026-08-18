"""Compatibility alias for canonical Modal environment utilities."""

import sys

try:
    from voidcube.infrastructure.execution.environments import modal_utils as _implementation
except (ModuleNotFoundError, ImportError):
    from src.voidcube.infrastructure.execution.environments import modal_utils as _implementation

sys.modules[__name__] = _implementation

"""Compatibility alias for canonical managed Modal environment."""

import sys

try:
    from voidcube.infrastructure.execution.environments import managed_modal as _implementation
except (ModuleNotFoundError, ImportError):
    from src.voidcube.infrastructure.execution.environments import managed_modal as _implementation

sys.modules[__name__] = _implementation

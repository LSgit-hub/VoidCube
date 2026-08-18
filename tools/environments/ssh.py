"""Compatibility alias for canonical SSH environment."""

import sys

try:
    from voidcube.infrastructure.execution.environments import ssh as _implementation
except (ModuleNotFoundError, ImportError):
    from src.voidcube.infrastructure.execution.environments import ssh as _implementation

sys.modules[__name__] = _implementation

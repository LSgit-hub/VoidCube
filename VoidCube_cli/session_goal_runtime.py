"""Compatibility alias for canonical CLI adapter."""

import sys

try:
    from voidcube.interfaces.cli import session_goal_runtime as _implementation
except (ModuleNotFoundError, ImportError):
    from src.voidcube.interfaces.cli import session_goal_runtime as _implementation

sys.modules[__name__] = _implementation

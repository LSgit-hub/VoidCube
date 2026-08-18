"""Compatibility alias for canonical Agent iteration control."""

import sys

try:
    from voidcube.domain.agent import iteration_control as _implementation
except (ModuleNotFoundError, ImportError):
    from src.voidcube.domain.agent import iteration_control as _implementation

sys.modules[__name__] = _implementation

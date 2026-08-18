"""Compatibility alias for the canonical Supervisor planning runtime."""

import sys

try:
    from voidcube.systems.supervisor import planning_runtime as _implementation
except (ModuleNotFoundError, ImportError):
    from src.voidcube.systems.supervisor import planning_runtime as _implementation

sys.modules[__name__] = _implementation

"""Compatibility alias for canonical Supervisor module."""

import sys

try:
    from voidcube.systems.supervisor import trace_runtime as _implementation
except (ModuleNotFoundError, ImportError):
    from src.voidcube.systems.supervisor import trace_runtime as _implementation

sys.modules[__name__] = _implementation

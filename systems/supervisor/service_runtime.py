"""Compatibility alias for the canonical Supervisor service runtime."""

import sys

try:
    from voidcube.systems.supervisor import service_runtime as _implementation
except (ModuleNotFoundError, ImportError):
    from src.voidcube.systems.supervisor import service_runtime as _implementation

sys.modules[__name__] = _implementation

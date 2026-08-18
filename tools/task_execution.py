"""Compatibility alias for the canonical task execution contracts."""

import sys

try:
    from voidcube.infrastructure.execution import task_execution as _implementation
except (ModuleNotFoundError, ImportError):
    from src.voidcube.infrastructure.execution import task_execution as _implementation

sys.modules[__name__] = _implementation

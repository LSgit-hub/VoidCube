"""Compatibility alias for canonical Supervisor module."""

import sys

try:
    from voidcube.systems.supervisor import autonomous_task_owner_session_service as _implementation
except (ModuleNotFoundError, ImportError):
    from src.voidcube.systems.supervisor import autonomous_task_owner_session_service as _implementation

sys.modules[__name__] = _implementation

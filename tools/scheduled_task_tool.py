"""Compatibility alias for canonical scheduled task tool."""

import sys

try:
    from voidcube.extensions.tools import scheduled_task_tool as _implementation
except (ModuleNotFoundError, ImportError):
    from src.voidcube.extensions.tools import scheduled_task_tool as _implementation

sys.modules[__name__] = _implementation

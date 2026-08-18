"""Compatibility module alias for canonical supervisor scheduling store."""

import sys

try:
    from voidcube.systems.supervisor import scheduled_tasks as _implementation
except (ModuleNotFoundError, ImportError):
    from src.voidcube.systems.supervisor import scheduled_tasks as _implementation

sys.modules[__name__] = _implementation

"""Compatibility alias for canonical Windows host execution."""

import sys

try:
    from voidcube.infrastructure.execution import windows_host_executor as _implementation
except (ModuleNotFoundError, ImportError):
    from src.voidcube.infrastructure.execution import windows_host_executor as _implementation

sys.modules[__name__] = _implementation

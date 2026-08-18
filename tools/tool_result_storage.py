"""Compatibility alias for canonical tool result persistence hooks."""

import sys

try:
    from voidcube.infrastructure.persistence import tool_result_storage as _implementation
except (ModuleNotFoundError, ImportError):
    from src.voidcube.infrastructure.persistence import tool_result_storage as _implementation

sys.modules[__name__] = _implementation

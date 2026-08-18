"""Compatibility alias for canonical sandbox code execution."""

import sys

try:
    from voidcube.infrastructure.execution import code_execution_tool as _implementation
except (ModuleNotFoundError, ImportError):
    from src.voidcube.infrastructure.execution import code_execution_tool as _implementation

sys.modules[__name__] = _implementation

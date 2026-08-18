"""Compatibility alias for execution approval guards."""

import sys

try:
    from voidcube.infrastructure.execution import approval as _implementation
except (ModuleNotFoundError, ImportError):
    from src.voidcube.infrastructure.execution import approval as _implementation

sys.modules[__name__] = _implementation

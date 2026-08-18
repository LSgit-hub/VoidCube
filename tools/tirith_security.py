"""Compatibility alias for the canonical command security scanner."""

import sys

try:
    from voidcube.infrastructure.execution import tirith_security as _implementation
except (ModuleNotFoundError, ImportError):
    from src.voidcube.infrastructure.execution import tirith_security as _implementation

sys.modules[__name__] = _implementation

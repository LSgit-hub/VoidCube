"""Compatibility alias for the canonical Supervisor service."""

import sys

try:
    from voidcube.systems.supervisor import supervisor as _implementation
except (ModuleNotFoundError, ImportError):
    from src.voidcube.systems.supervisor import supervisor as _implementation

sys.modules[__name__] = _implementation

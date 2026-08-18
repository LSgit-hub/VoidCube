"""Compatibility alias for canonical governance runtime migration."""

import sys

try:
    from voidcube.systems import governance_runtime_migration as _implementation
except (ModuleNotFoundError, ImportError):
    from src.voidcube.systems import governance_runtime_migration as _implementation

sys.modules[__name__] = _implementation


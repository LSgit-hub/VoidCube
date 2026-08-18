"""Compatibility alias for canonical side-effect action journal."""

import sys

try:
    from voidcube.infrastructure.persistence import action_journal as _implementation
except (ModuleNotFoundError, ImportError):
    from src.voidcube.infrastructure.persistence import action_journal as _implementation

sys.modules[__name__] = _implementation

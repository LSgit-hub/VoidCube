"""Compatibility alias for canonical checkpoint persistence."""

import sys

try:
    from voidcube.infrastructure.persistence import checkpoint_manager as _implementation
except (ModuleNotFoundError, ImportError):
    from src.voidcube.infrastructure.persistence import checkpoint_manager as _implementation

sys.modules[__name__] = _implementation

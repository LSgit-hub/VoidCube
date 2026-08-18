"""Compatibility alias for canonical atomic file writer."""

import sys

try:
    from voidcube.infrastructure.persistence import file_atomic_writer as _implementation
except (ModuleNotFoundError, ImportError):
    from src.voidcube.infrastructure.persistence import file_atomic_writer as _implementation

sys.modules[__name__] = _implementation

"""Compatibility alias for canonical evolution boundary contracts."""

import sys

try:
    from voidcube.systems import evolution_boundary as _implementation
except (ModuleNotFoundError, ImportError):
    from src.voidcube.systems import evolution_boundary as _implementation

sys.modules[__name__] = _implementation


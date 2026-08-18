"""Compatibility alias for canonical Mem source binding."""

import sys

try:
    from voidcube.systems import mem_source_binding as _implementation
except (ModuleNotFoundError, ImportError):
    from src.voidcube.systems import mem_source_binding as _implementation

sys.modules[__name__] = _implementation

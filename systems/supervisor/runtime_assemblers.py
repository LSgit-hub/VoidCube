"""Compatibility alias for canonical Supervisor runtime assemblers."""

import sys

try:
    from voidcube.systems.supervisor import runtime_assemblers as _implementation
except (ModuleNotFoundError, ImportError):
    from src.voidcube.systems.supervisor import runtime_assemblers as _implementation

sys.modules[__name__] = _implementation

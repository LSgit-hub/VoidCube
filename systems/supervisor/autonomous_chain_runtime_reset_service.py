"""Compatibility alias for canonical Supervisor module."""

import sys

try:
    from voidcube.systems.supervisor import autonomous_chain_runtime_reset_service as _implementation
except (ModuleNotFoundError, ImportError):
    from src.voidcube.systems.supervisor import autonomous_chain_runtime_reset_service as _implementation

sys.modules[__name__] = _implementation

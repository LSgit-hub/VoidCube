"""Compatibility alias for the canonical autonomous chain store."""

import sys

try:
    from voidcube.systems.supervisor import autonomous_chain_store as _implementation
except (ModuleNotFoundError, ImportError):
    from src.voidcube.systems.supervisor import autonomous_chain_store as _implementation

sys.modules[__name__] = _implementation


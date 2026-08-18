"""Compatibility alias for the canonical supervisor endogenous_state_repository adapter."""

import sys

try:
    from voidcube.systems.supervisor import endogenous_state_repository as _implementation
except (ModuleNotFoundError, ImportError):
    from src.voidcube.systems.supervisor import endogenous_state_repository as _implementation

sys.modules[__name__] = _implementation

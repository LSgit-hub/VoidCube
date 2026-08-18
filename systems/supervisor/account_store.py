"""Compatibility alias for the canonical supervisor account store."""

import sys

try:
    from voidcube.systems.supervisor import account_store as _implementation
except (ModuleNotFoundError, ImportError):
    from src.voidcube.systems.supervisor import account_store as _implementation

sys.modules[__name__] = _implementation

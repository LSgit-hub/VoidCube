"""Compatibility alias for the canonical Provider pool service."""

import sys

try:
    from voidcube.systems.supervisor import provider_pool_service as _implementation
except (ModuleNotFoundError, ImportError):
    from src.voidcube.systems.supervisor import provider_pool_service as _implementation

sys.modules[__name__] = _implementation

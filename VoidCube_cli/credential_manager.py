"""Compatibility alias for canonical credential manager."""

import sys

try:
    from voidcube.infrastructure.providers import credential_manager as _implementation
except (ModuleNotFoundError, ImportError):
    from src.voidcube.infrastructure.providers import credential_manager as _implementation

sys.modules[__name__] = _implementation

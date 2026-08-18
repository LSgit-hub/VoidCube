"""Compatibility alias for canonical sandbox environment passthrough."""

import sys

try:
    from voidcube.infrastructure.execution import env_passthrough as _implementation
except (ModuleNotFoundError, ImportError):
    from src.voidcube.infrastructure.execution import env_passthrough as _implementation

sys.modules[__name__] = _implementation

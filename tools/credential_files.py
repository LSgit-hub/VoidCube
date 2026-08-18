"""Compatibility alias for canonical credential file mounts."""

import sys

try:
    from voidcube.infrastructure.execution import credential_files as _implementation
except (ModuleNotFoundError, ImportError):
    from src.voidcube.infrastructure.execution import credential_files as _implementation

sys.modules[__name__] = _implementation

"""Compatibility alias for the Podman execution sandbox helper."""

import sys

try:
    from voidcube.infrastructure.execution import podman_sandbox as _implementation
except (ModuleNotFoundError, ImportError):
    from src.voidcube.infrastructure.execution import podman_sandbox as _implementation

sys.modules[__name__] = _implementation

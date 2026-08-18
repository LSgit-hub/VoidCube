"""Compatibility alias for canonical Docker and Podman environments."""

import sys

try:
    from voidcube.infrastructure.execution.environments import docker as _implementation
except (ModuleNotFoundError, ImportError):
    from src.voidcube.infrastructure.execution.environments import docker as _implementation

sys.modules[__name__] = _implementation

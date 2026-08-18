"""Compatibility alias for canonical Daytona environment."""

import sys

try:
    from voidcube.infrastructure.execution.environments import daytona as _implementation
except (ModuleNotFoundError, ImportError):
    from src.voidcube.infrastructure.execution.environments import daytona as _implementation

sys.modules[__name__] = _implementation

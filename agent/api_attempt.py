"""Compatibility alias for canonical API attempt state."""

import sys

try:
    from voidcube.domain.agent import api_attempt as _implementation
except (ModuleNotFoundError, ImportError):
    from src.voidcube.domain.agent import api_attempt as _implementation

sys.modules[__name__] = _implementation

"""Compatibility alias for canonical response disposition rules."""

import sys

try:
    from voidcube.domain.agent import response_disposition as _implementation
except (ModuleNotFoundError, ImportError):
    from src.voidcube.domain.agent import response_disposition as _implementation

sys.modules[__name__] = _implementation

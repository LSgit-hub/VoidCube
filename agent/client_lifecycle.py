"""Compatibility alias for canonical Agent client lifecycle."""

import sys

try:
    from voidcube.runtime.agent import client_lifecycle as _implementation
except (ModuleNotFoundError, ImportError):
    from src.voidcube.runtime.agent import client_lifecycle as _implementation

sys.modules[__name__] = _implementation

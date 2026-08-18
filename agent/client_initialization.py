"""Compatibility alias for canonical Agent client initialization."""

import sys

try:
    from voidcube.runtime.agent import client_initialization as _implementation
except (ModuleNotFoundError, ImportError):
    from src.voidcube.runtime.agent import client_initialization as _implementation

sys.modules[__name__] = _implementation

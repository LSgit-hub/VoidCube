"""Compatibility alias for canonical toolset definitions."""

import sys

try:
    from voidcube.extensions.tools import toolsets as _implementation
except (ModuleNotFoundError, ImportError):
    from src.voidcube.extensions.tools import toolsets as _implementation

sys.modules[__name__] = _implementation

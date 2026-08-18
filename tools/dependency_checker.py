"""Compatibility alias for canonical runtime dependency checks."""

import sys

try:
    from voidcube.extensions.tools import dependency_checker as _implementation
except (ModuleNotFoundError, ImportError):
    from src.voidcube.extensions.tools import dependency_checker as _implementation

sys.modules[__name__] = _implementation

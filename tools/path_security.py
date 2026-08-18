"""Compatibility alias for canonical file path security helpers."""

import sys

try:
    from voidcube.extensions.tools.files import path_security as _implementation
except (ModuleNotFoundError, ImportError):
    from src.voidcube.extensions.tools.files import path_security as _implementation

sys.modules[__name__] = _implementation

"""Compatibility alias for canonical tool backend policy helpers."""

import sys

try:
    from voidcube.extensions.tools import backend_helpers as _implementation
except (ModuleNotFoundError, ImportError):
    from src.voidcube.extensions.tools import backend_helpers as _implementation

sys.modules[__name__] = _implementation

"""Compatibility alias for canonical Camofox browser backend."""

import sys

try:
    from voidcube.extensions.tools.browser import browser_camofox as _implementation
except (ModuleNotFoundError, ImportError):
    from src.voidcube.extensions.tools.browser import browser_camofox as _implementation

sys.modules[__name__] = _implementation

"""Compatibility alias for canonical Camofox browser state."""

import sys

try:
    from voidcube.extensions.tools.browser import browser_camofox_state as _implementation
except (ModuleNotFoundError, ImportError):
    from src.voidcube.extensions.tools.browser import browser_camofox_state as _implementation

sys.modules[__name__] = _implementation

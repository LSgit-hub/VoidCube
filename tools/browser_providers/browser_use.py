"""Compatibility alias for canonical Browser Use adapter."""

import sys

try:
    from voidcube.extensions.tools.browser.providers import browser_use as _implementation
except (ModuleNotFoundError, ImportError):
    from src.voidcube.extensions.tools.browser.providers import browser_use as _implementation

sys.modules[__name__] = _implementation

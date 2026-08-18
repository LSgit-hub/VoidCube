"""Compatibility alias for canonical Browserbase adapter."""

import sys

try:
    from voidcube.extensions.tools.browser.providers import browserbase as _implementation
except (ModuleNotFoundError, ImportError):
    from src.voidcube.extensions.tools.browser.providers import browserbase as _implementation

sys.modules[__name__] = _implementation

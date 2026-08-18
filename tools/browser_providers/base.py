"""Compatibility alias for canonical browser provider contracts."""

import sys

try:
    from voidcube.extensions.tools.browser.providers import base as _implementation
except (ModuleNotFoundError, ImportError):
    from src.voidcube.extensions.tools.browser.providers import base as _implementation

sys.modules[__name__] = _implementation

"""Compatibility alias for canonical Firecrawl browser adapter."""

import sys

try:
    from voidcube.extensions.tools.browser.providers import firecrawl as _implementation
except (ModuleNotFoundError, ImportError):
    from src.voidcube.extensions.tools.browser.providers import firecrawl as _implementation

sys.modules[__name__] = _implementation

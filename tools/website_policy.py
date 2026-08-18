"""Compatibility alias for canonical website access policy."""

import sys

try:
    from voidcube.extensions.tools.web import website_policy as _implementation
except (ModuleNotFoundError, ImportError):
    from src.voidcube.extensions.tools.web import website_policy as _implementation

sys.modules[__name__] = _implementation

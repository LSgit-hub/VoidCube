"""Compatibility alias for canonical URL safety checks."""

import sys

try:
    from voidcube.extensions.tools.web import url_safety as _implementation
except (ModuleNotFoundError, ImportError):
    from src.voidcube.extensions.tools.web import url_safety as _implementation

sys.modules[__name__] = _implementation

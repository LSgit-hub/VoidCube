"""Compatibility alias for canonical fuzzy file matching."""

import sys

try:
    from voidcube.extensions.tools.files import fuzzy_match as _implementation
except (ModuleNotFoundError, ImportError):
    from src.voidcube.extensions.tools.files import fuzzy_match as _implementation

sys.modules[__name__] = _implementation

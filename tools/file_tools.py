"""Compatibility alias for canonical file tool registrations."""

import sys

try:
    from voidcube.extensions.tools.files import file_tools as _implementation
except (ModuleNotFoundError, ImportError):
    from src.voidcube.extensions.tools.files import file_tools as _implementation

sys.modules[__name__] = _implementation

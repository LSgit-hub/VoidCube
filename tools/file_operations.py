"""Compatibility alias for canonical file operation services."""

import sys

try:
    from voidcube.extensions.tools.files import file_operations as _implementation
except (ModuleNotFoundError, ImportError):
    from src.voidcube.extensions.tools.files import file_operations as _implementation

sys.modules[__name__] = _implementation

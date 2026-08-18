"""Compatibility alias for canonical binary file extension policy."""

import sys

try:
    from voidcube.extensions.tools.files import binary_extensions as _implementation
except (ModuleNotFoundError, ImportError):
    from src.voidcube.extensions.tools.files import binary_extensions as _implementation

sys.modules[__name__] = _implementation

"""Compatibility alias for canonical V4A patch parsing."""

import sys

try:
    from voidcube.extensions.tools.files import patch_parser as _implementation
except (ModuleNotFoundError, ImportError):
    from src.voidcube.extensions.tools.files import patch_parser as _implementation

sys.modules[__name__] = _implementation

"""Compatibility alias for canonical vision tools."""

import sys

try:
    from voidcube.extensions.tools.media import vision_tools as _implementation
except (ModuleNotFoundError, ImportError):
    from src.voidcube.extensions.tools.media import vision_tools as _implementation

sys.modules[__name__] = _implementation

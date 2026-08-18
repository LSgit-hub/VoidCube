"""Compatibility alias for canonical media playback tools."""

import sys

try:
    from voidcube.extensions.tools.media import media_tool as _implementation
except (ModuleNotFoundError, ImportError):
    from src.voidcube.extensions.tools.media import media_tool as _implementation

sys.modules[__name__] = _implementation

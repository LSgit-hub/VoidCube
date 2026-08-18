"""Compatibility alias for canonical deployment preset catalog."""

import sys

try:
    from voidcube.extensions.tools import preset_engine as _implementation
except (ModuleNotFoundError, ImportError):
    from src.voidcube.extensions.tools import preset_engine as _implementation

sys.modules[__name__] = _implementation

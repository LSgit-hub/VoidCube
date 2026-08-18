"""Compatibility alias for canonical skill slash-command services."""

import sys

try:
    from voidcube.extensions.skills import commands as _implementation
except (ModuleNotFoundError, ImportError):
    from src.voidcube.extensions.skills import commands as _implementation

sys.modules[__name__] = _implementation

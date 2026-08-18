"""Compatibility alias for canonical desktop service control."""

import sys

try:
    from voidcube.interfaces.desktop import desktop_control as _implementation
except (ModuleNotFoundError, ImportError):
    from src.voidcube.interfaces.desktop import desktop_control as _implementation

sys.modules[__name__] = _implementation

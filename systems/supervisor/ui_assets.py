"""Compatibility alias for canonical Supervisor module."""

import sys

try:
    from voidcube.systems.supervisor import ui_assets as _implementation
except (ModuleNotFoundError, ImportError):
    from src.voidcube.systems.supervisor import ui_assets as _implementation

sys.modules[__name__] = _implementation

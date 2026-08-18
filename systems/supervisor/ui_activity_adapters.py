"""Compatibility alias for the canonical supervisor ui_activity_adapters adapter."""

import sys

try:
    from voidcube.systems.supervisor import ui_activity_adapters as _implementation
except (ModuleNotFoundError, ImportError):
    from src.voidcube.systems.supervisor import ui_activity_adapters as _implementation

sys.modules[__name__] = _implementation

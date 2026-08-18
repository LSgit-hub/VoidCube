"""Compatibility alias for canonical CLI subagent display services."""

import sys

try:
    from voidcube.interfaces.cli import subagent_display as _implementation
except (ModuleNotFoundError, ImportError):
    from src.voidcube.interfaces.cli import subagent_display as _implementation

sys.modules[__name__] = _implementation

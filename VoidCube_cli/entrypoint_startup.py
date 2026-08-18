"""Compatibility alias for canonical CLI startup preparation."""

import sys

try:
    from voidcube.interfaces.cli import entrypoint_startup as _implementation
except (ModuleNotFoundError, ImportError):
    from src.voidcube.interfaces.cli import entrypoint_startup as _implementation

sys.modules[__name__] = _implementation

"""Compatibility alias for the canonical CLI operations dashboard."""

import sys

try:
    from voidcube.interfaces.cli.ops import dashboard as _implementation
except (ModuleNotFoundError, ImportError):
    from src.voidcube.interfaces.cli.ops import dashboard as _implementation

sys.modules[__name__] = _implementation

"""Compatibility alias for the canonical CLI input loop."""

import sys
try:
    from voidcube.interfaces.cli.lifecycle import input_loop as _implementation
except (ModuleNotFoundError, ImportError):
    from src.voidcube.interfaces.cli.lifecycle import input_loop as _implementation
sys.modules[__name__] = _implementation

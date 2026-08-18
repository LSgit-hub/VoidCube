"""Compatibility module alias for canonical model normalization."""

import sys

try:
    from voidcube.interfaces.cli import model_normalize as _implementation
except (ModuleNotFoundError, ImportError):
    from src.voidcube.interfaces.cli import model_normalize as _implementation

sys.modules[__name__] = _implementation

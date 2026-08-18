"""Compatibility module alias for canonical CLI attachments."""

import sys

try:
    from voidcube.interfaces.cli import attachments as _implementation
except (ModuleNotFoundError, ImportError):
    from src.voidcube.interfaces.cli import attachments as _implementation

sys.modules[__name__] = _implementation

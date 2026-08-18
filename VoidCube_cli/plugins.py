"""Compatibility module alias for canonical CLI plugin adapters."""

import sys

try:
    from voidcube.extensions.plugins import cli_adapter as _implementation
except (ModuleNotFoundError, ImportError):
    from src.voidcube.extensions.plugins import cli_adapter as _implementation

sys.modules[__name__] = _implementation

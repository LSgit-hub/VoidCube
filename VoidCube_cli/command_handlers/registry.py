"""Compatibility alias for canonical CLI command composition."""

import sys

try:
    from voidcube.interfaces.cli.commands import registry as _implementation
except (ModuleNotFoundError, ImportError):
    from src.voidcube.interfaces.cli.commands import registry as _implementation

sys.modules[__name__] = _implementation

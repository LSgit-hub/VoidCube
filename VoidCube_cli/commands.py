"""Compatibility module alias for canonical CLI slash-command catalog."""

import sys

try:
    from voidcube.interfaces.cli.commands import catalog as _implementation
except (ModuleNotFoundError, ImportError):
    from src.voidcube.interfaces.cli.commands import catalog as _implementation

sys.modules[__name__] = _implementation

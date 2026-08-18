"""Compatibility alias for canonical CLI command handler preset."""

import sys

try:
    from voidcube.interfaces.cli.commands.handlers import preset as _implementation
except (ModuleNotFoundError, ImportError):
    from src.voidcube.interfaces.cli.commands.handlers import preset as _implementation

sys.modules[__name__] = _implementation

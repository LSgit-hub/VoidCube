"""Compatibility alias for canonical CLI command handler goal."""

import sys

try:
    from voidcube.interfaces.cli.commands.handlers import goal as _implementation
except (ModuleNotFoundError, ImportError):
    from src.voidcube.interfaces.cli.commands.handlers import goal as _implementation

sys.modules[__name__] = _implementation

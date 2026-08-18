"""Compatibility alias for canonical CLI command handler chat_blocks."""

import sys

try:
    from voidcube.interfaces.cli.commands.handlers import chat_blocks as _implementation
except (ModuleNotFoundError, ImportError):
    from src.voidcube.interfaces.cli.commands.handlers import chat_blocks as _implementation

sys.modules[__name__] = _implementation

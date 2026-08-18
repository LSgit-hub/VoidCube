"""Compatibility alias for canonical CLI entrypoint parser_operations."""

import sys

try:
    from voidcube.interfaces.cli.entrypoints import parser_operations as _implementation
except (ModuleNotFoundError, ImportError):
    from src.voidcube.interfaces.cli.entrypoints import parser_operations as _implementation

sys.modules[__name__] = _implementation

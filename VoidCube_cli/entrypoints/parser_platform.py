"""Compatibility alias for canonical CLI entrypoint parser_platform."""

import sys

try:
    from voidcube.interfaces.cli.entrypoints import parser_platform as _implementation
except (ModuleNotFoundError, ImportError):
    from src.voidcube.interfaces.cli.entrypoints import parser_platform as _implementation

sys.modules[__name__] = _implementation

"""Compatibility alias for canonical CLI entrypoint parser."""

import sys

try:
    from voidcube.interfaces.cli.entrypoints import parser as _implementation
except (ModuleNotFoundError, ImportError):
    from src.voidcube.interfaces.cli.entrypoints import parser as _implementation

sys.modules[__name__] = _implementation

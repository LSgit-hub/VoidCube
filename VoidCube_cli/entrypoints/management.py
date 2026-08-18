"""Compatibility alias for canonical CLI entrypoint management."""

import sys

try:
    from voidcube.interfaces.cli.entrypoints import management as _implementation
except (ModuleNotFoundError, ImportError):
    from src.voidcube.interfaces.cli.entrypoints import management as _implementation

sys.modules[__name__] = _implementation

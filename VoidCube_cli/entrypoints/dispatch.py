"""Compatibility alias for canonical CLI entrypoint dispatch."""

import sys

try:
    from voidcube.interfaces.cli.entrypoints import dispatch as _implementation
except (ModuleNotFoundError, ImportError):
    from src.voidcube.interfaces.cli.entrypoints import dispatch as _implementation

sys.modules[__name__] = _implementation

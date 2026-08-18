"""Compatibility alias for canonical CLI entrypoint provider."""

import sys

try:
    from voidcube.interfaces.cli.entrypoints import provider as _implementation
except (ModuleNotFoundError, ImportError):
    from src.voidcube.interfaces.cli.entrypoints import provider as _implementation

sys.modules[__name__] = _implementation

"""Compatibility alias for canonical CLI entrypoint session."""

import sys

try:
    from voidcube.interfaces.cli.entrypoints import session as _implementation
except (ModuleNotFoundError, ImportError):
    from src.voidcube.interfaces.cli.entrypoints import session as _implementation

sys.modules[__name__] = _implementation

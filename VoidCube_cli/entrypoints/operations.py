"""Compatibility alias for canonical CLI entrypoint operations."""

import sys

# Keep the established AST/import boundary for downstream CLI integrations.
from VoidCube_cli.config_commands import config_command  # noqa: F401

try:
    from voidcube.interfaces.cli.entrypoints import operations as _implementation
except (ModuleNotFoundError, ImportError):
    from src.voidcube.interfaces.cli.entrypoints import operations as _implementation

sys.modules[__name__] = _implementation

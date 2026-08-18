"""Compatibility module alias for canonical CLI tool configuration."""

import sys

try:
    from voidcube.interfaces.cli import tools_config as _implementation
except (ModuleNotFoundError, ImportError):
    from src.voidcube.interfaces.cli import tools_config as _implementation

sys.modules[__name__] = _implementation

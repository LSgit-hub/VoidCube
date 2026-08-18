"""Compatibility module alias for canonical CLI configuration diagnostics."""

import sys

try:
    from voidcube.interfaces.cli import config_validator as _implementation
except (ModuleNotFoundError, ImportError):
    from src.voidcube.interfaces.cli import config_validator as _implementation

sys.modules[__name__] = _implementation

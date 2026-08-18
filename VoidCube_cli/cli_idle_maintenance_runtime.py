"""Compatibility alias for the canonical CLI idle maintenance runtime."""

import sys
try:
    from voidcube.interfaces.cli.lifecycle import idle_maintenance as _implementation
except (ModuleNotFoundError, ImportError):
    from src.voidcube.interfaces.cli.lifecycle import idle_maintenance as _implementation
sys.modules[__name__] = _implementation

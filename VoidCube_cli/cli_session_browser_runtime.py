"""Compatibility alias for the canonical CLI session browser runtime."""

import sys
try:
    from voidcube.interfaces.cli import session_browser_runtime as _implementation
except (ModuleNotFoundError, ImportError):
    from src.voidcube.interfaces.cli import session_browser_runtime as _implementation
sys.modules[__name__] = _implementation

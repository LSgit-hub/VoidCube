"""Compatibility alias for canonical chat render state."""
import sys
try:
    from voidcube.interfaces.cli.chat import render_state as _implementation
except (ModuleNotFoundError, ImportError):
    from src.voidcube.interfaces.cli.chat import render_state as _implementation
sys.modules[__name__] = _implementation

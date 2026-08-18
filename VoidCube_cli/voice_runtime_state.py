"""Compatibility alias for the canonical CLI voice runtime state."""

import sys
try:
    from voidcube.interfaces.cli import voice_runtime_state as _implementation
except (ModuleNotFoundError, ImportError):
    from src.voidcube.interfaces.cli import voice_runtime_state as _implementation
sys.modules[__name__] = _implementation

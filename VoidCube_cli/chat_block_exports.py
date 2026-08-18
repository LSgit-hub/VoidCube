"""Compatibility alias for canonical chat block exports."""
import sys
try:
    from voidcube.interfaces.cli.chat import block_exports as _implementation
except (ModuleNotFoundError, ImportError):
    from src.voidcube.interfaces.cli.chat import block_exports as _implementation
sys.modules[__name__] = _implementation

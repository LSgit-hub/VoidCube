"""Compatibility alias for canonical chat block store."""
import sys
try:
    from voidcube.interfaces.cli.chat import block_store as _implementation
except (ModuleNotFoundError, ImportError):
    from src.voidcube.interfaces.cli.chat import block_store as _implementation
sys.modules[__name__] = _implementation

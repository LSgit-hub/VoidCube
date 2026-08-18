"""Compatibility alias for canonical chat stream processor."""
import sys
try:
    from voidcube.interfaces.cli.chat import stream_processor as _implementation
except (ModuleNotFoundError, ImportError):
    from src.voidcube.interfaces.cli.chat import stream_processor as _implementation
sys.modules[__name__] = _implementation

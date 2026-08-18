"""Compatibility alias for canonical chat stream renderer."""
import sys
try:
    from voidcube.interfaces.cli.chat import stream_renderer as _implementation
except (ModuleNotFoundError, ImportError):
    from src.voidcube.interfaces.cli.chat import stream_renderer as _implementation
sys.modules[__name__] = _implementation

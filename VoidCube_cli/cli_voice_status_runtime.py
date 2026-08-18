"""Compatibility alias for canonical voice status runtime."""

try:
    from voidcube.interfaces.cli.voice_status_runtime import *  # noqa: F401,F403
except ModuleNotFoundError:
    from src.voidcube.interfaces.cli.voice_status_runtime import *  # noqa: F401,F403

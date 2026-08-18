"""Compatibility alias for canonical voice recording runtime."""

try:
    from voidcube.interfaces.cli.voice_recording_runtime import *  # noqa: F401,F403
except ModuleNotFoundError:
    from src.voidcube.interfaces.cli.voice_recording_runtime import *  # noqa: F401,F403

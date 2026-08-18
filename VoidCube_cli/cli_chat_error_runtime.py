"""Compatibility alias for canonical chat error runtime."""

try:
    from voidcube.interfaces.cli.chat_error_runtime import *  # noqa: F401,F403
except ModuleNotFoundError:
    from src.voidcube.interfaces.cli.chat_error_runtime import *  # noqa: F401,F403

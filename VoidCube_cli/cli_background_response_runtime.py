"""Compatibility alias for canonical background response runtime."""

try:
    from voidcube.interfaces.cli.background_response_runtime import *  # noqa: F401,F403
except ModuleNotFoundError:
    from src.voidcube.interfaces.cli.background_response_runtime import *  # noqa: F401,F403

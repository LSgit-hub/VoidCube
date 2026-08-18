"""Compatibility alias for canonical session teardown runtime."""

try:
    from voidcube.interfaces.cli.session_teardown_runtime import *  # noqa: F401,F403
except ModuleNotFoundError:
    from src.voidcube.interfaces.cli.session_teardown_runtime import *  # noqa: F401,F403

"""Compatibility alias for canonical middle status runtime."""

try:
    from voidcube.interfaces.cli.middle_status_runtime import *  # noqa: F401,F403
except ModuleNotFoundError:
    from src.voidcube.interfaces.cli.middle_status_runtime import *  # noqa: F401,F403

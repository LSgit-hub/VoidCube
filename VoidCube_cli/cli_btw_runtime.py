"""Compatibility alias for canonical BTW runtime."""

try:
    from voidcube.interfaces.cli.btw_runtime import *  # noqa: F401,F403
except ModuleNotFoundError:
    from src.voidcube.interfaces.cli.btw_runtime import *  # noqa: F401,F403

"""Compatibility alias for canonical CLI runtime credentials."""

try:
    from voidcube.interfaces.cli.runtime_credentials import *  # noqa: F401,F403
except ModuleNotFoundError:
    from src.voidcube.interfaces.cli.runtime_credentials import *  # noqa: F401,F403

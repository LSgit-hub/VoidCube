"""Compatibility alias for canonical session resume runtime."""

try:
    from voidcube.interfaces.cli.session_resume import *  # noqa: F401,F403
except ModuleNotFoundError:
    from src.voidcube.interfaces.cli.session_resume import *  # noqa: F401,F403

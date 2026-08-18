"""Compatibility alias for canonical single-query resume runtime."""

try:
    from voidcube.interfaces.cli.single_query_resume import *  # noqa: F401,F403
except ModuleNotFoundError:
    from src.voidcube.interfaces.cli.single_query_resume import *  # noqa: F401,F403

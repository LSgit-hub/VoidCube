"""Compatibility alias for canonical exit summary runtime."""

try:
    from voidcube.interfaces.cli.exit_summary_runtime import *  # noqa: F401,F403
except ModuleNotFoundError:
    from src.voidcube.interfaces.cli.exit_summary_runtime import *  # noqa: F401,F403

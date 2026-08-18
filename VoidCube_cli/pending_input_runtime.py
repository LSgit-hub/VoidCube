"""Compatibility alias for canonical pending-input runtime."""

try:
    from voidcube.interfaces.cli.pending_input_runtime import *  # noqa: F401,F403
except ModuleNotFoundError:
    from src.voidcube.interfaces.cli.pending_input_runtime import *  # noqa: F401,F403

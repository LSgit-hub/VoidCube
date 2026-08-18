"""Compatibility alias for the canonical scheduler display projector."""

try:
    from voidcube.interfaces.cli.scheduler_display_projector import *  # noqa: F401,F403
except ModuleNotFoundError:
    from src.voidcube.interfaces.cli.scheduler_display_projector import *  # noqa: F401,F403

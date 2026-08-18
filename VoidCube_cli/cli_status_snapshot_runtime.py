"""Compatibility alias for canonical status snapshot runtime."""

try:
    from voidcube.interfaces.cli.status_snapshot_runtime import *  # noqa: F401,F403
except ModuleNotFoundError:
    from src.voidcube.interfaces.cli.status_snapshot_runtime import *  # noqa: F401,F403

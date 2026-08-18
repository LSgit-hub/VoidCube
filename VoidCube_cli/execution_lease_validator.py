"""Compatibility alias for canonical execution-lease validation."""

try:
    from voidcube.interfaces.cli.execution_lease_validator import *  # noqa: F401,F403
except ModuleNotFoundError:
    from src.voidcube.interfaces.cli.execution_lease_validator import *  # noqa: F401,F403

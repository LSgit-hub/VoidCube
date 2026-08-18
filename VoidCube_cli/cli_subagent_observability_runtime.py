"""Compatibility alias for canonical subagent observability runtime."""

try:
    from voidcube.interfaces.cli.subagent_observability_runtime import *  # noqa: F401,F403
except ModuleNotFoundError:
    from src.voidcube.interfaces.cli.subagent_observability_runtime import *  # noqa: F401,F403

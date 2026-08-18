"""Compatibility alias for canonical turn input preparation."""

try:
    from voidcube.interfaces.cli.turn.input_preparation import *  # noqa: F401,F403
except ModuleNotFoundError:
    from src.voidcube.interfaces.cli.turn.input_preparation import *  # noqa: F401,F403

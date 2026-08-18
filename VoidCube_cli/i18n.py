"""Compatibility alias for the canonical CLI internationalization service."""

try:
    from voidcube.interfaces.cli.i18n import *  # noqa: F401,F403
except (ModuleNotFoundError, ImportError):
    from src.voidcube.interfaces.cli.i18n import *  # noqa: F401,F403

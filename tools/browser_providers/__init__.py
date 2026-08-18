"""Compatibility alias package for canonical browser providers."""

try:
    from voidcube.extensions.tools.browser.providers import *  # noqa: F401,F403
except ModuleNotFoundError:
    from src.voidcube.extensions.tools.browser.providers import *  # noqa: F401,F403

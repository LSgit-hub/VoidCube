"""Compatibility alias for canonical mixture-of-agents tool."""

import sys

try:
    from voidcube.extensions.tools import mixture_of_agents_tool as _implementation
except (ModuleNotFoundError, ImportError):
    from src.voidcube.extensions.tools import mixture_of_agents_tool as _implementation

sys.modules[__name__] = _implementation

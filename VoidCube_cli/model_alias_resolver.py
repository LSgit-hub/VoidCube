"""Compatibility alias for canonical provider model alias resolution."""

import sys

try:
    from voidcube.infrastructure.providers import model_alias_resolver as _implementation
except (ModuleNotFoundError, ImportError):
    from src.voidcube.infrastructure.providers import model_alias_resolver as _implementation

sys.modules[__name__] = _implementation

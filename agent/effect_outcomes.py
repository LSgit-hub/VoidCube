"""Compatibility alias for canonical Agent effect outcomes."""

import sys

try:
    from voidcube.domain.agent import effect_outcomes as _implementation
except (ModuleNotFoundError, ImportError):
    from src.voidcube.domain.agent import effect_outcomes as _implementation

sys.modules[__name__] = _implementation

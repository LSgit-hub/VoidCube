"""Compatibility alias for canonical application turn executor."""

import sys

try:
    from voidcube.application import single_turn_executor as _implementation
except (ModuleNotFoundError, ImportError):
    from src.voidcube.application import single_turn_executor as _implementation

sys.modules[__name__] = _implementation

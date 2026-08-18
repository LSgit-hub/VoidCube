"""Compatibility alias for the canonical agent prompt runtime."""

import sys

try:
    from voidcube.runtime.agent import prompt_builder as _implementation
except (ModuleNotFoundError, ImportError):
    from src.voidcube.runtime.agent import prompt_builder as _implementation

sys.modules[__name__] = _implementation

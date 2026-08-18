"""Compatibility alias for the canonical domain task profile."""

import sys

try:
    from voidcube.domain.tasks import runtime_profile as _implementation
except (ModuleNotFoundError, ImportError):
    from src.voidcube.domain.tasks import runtime_profile as _implementation

sys.modules[__name__] = _implementation

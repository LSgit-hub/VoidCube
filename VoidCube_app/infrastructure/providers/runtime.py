"""Compatibility module for canonical runtime provider resolution."""

from __future__ import annotations

import sys

try:
    from voidcube.infrastructure.providers import runtime as _implementation
except ModuleNotFoundError:
    from src.voidcube.infrastructure.providers import runtime as _implementation

sys.modules[__name__] = _implementation
setattr(sys.modules[__package__], "runtime", _implementation)

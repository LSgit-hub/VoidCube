"""Compatibility module for canonical provider endpoints."""

from __future__ import annotations

import sys

try:
    from voidcube.infrastructure.providers import endpoints as _implementation
except ModuleNotFoundError:
    from src.voidcube.infrastructure.providers import endpoints as _implementation

sys.modules[__name__] = _implementation
setattr(sys.modules[__package__], "endpoints", _implementation)

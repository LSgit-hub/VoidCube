"""Compatibility module for canonical application clock."""

from __future__ import annotations

import sys

try:
    from voidcube.infrastructure.shared import clock as _implementation
except ModuleNotFoundError:
    from src.voidcube.infrastructure.shared import clock as _implementation

sys.modules[__name__] = _implementation
setattr(sys.modules[__package__], "clock", _implementation)

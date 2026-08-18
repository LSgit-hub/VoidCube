"""Compatibility module for canonical reasoning helpers."""

from __future__ import annotations

import sys

try:
    from voidcube.infrastructure.shared import reasoning as _implementation
except ModuleNotFoundError:
    from src.voidcube.infrastructure.shared import reasoning as _implementation

sys.modules[__name__] = _implementation
setattr(sys.modules[__package__], "reasoning", _implementation)

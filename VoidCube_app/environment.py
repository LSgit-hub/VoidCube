"""Compatibility facade for canonical configuration environment loading."""

from __future__ import annotations

import sys

try:
    from voidcube.infrastructure.config import environment as _implementation
except ModuleNotFoundError:
    from src.voidcube.infrastructure.config import environment as _implementation

sys.modules[__name__] = _implementation
setattr(sys.modules[__package__], "environment", _implementation)

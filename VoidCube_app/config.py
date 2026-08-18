"""Compatibility facade for canonical configuration infrastructure."""

from __future__ import annotations

import sys

try:
    from voidcube.infrastructure.config import configuration as _implementation
except ModuleNotFoundError:
    from src.voidcube.infrastructure.config import configuration as _implementation

sys.modules[__name__] = _implementation
setattr(sys.modules[__package__], "config", _implementation)

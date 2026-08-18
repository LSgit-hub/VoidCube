"""Compatibility facade for canonical gateway presence client."""

from __future__ import annotations

import sys

try:
    from voidcube.infrastructure.gateway import presence as _implementation
except ModuleNotFoundError:
    from src.voidcube.infrastructure.gateway import presence as _implementation

sys.modules[__name__] = _implementation
setattr(sys.modules[__package__], "gateway", _implementation)

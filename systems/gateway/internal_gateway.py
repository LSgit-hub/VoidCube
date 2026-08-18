"""Compatibility facade for canonical infrastructure gateway."""

from __future__ import annotations

import sys

try:
    from voidcube.infrastructure.gateway import internal_gateway as _implementation
except ModuleNotFoundError:
    from src.voidcube.infrastructure.gateway import internal_gateway as _implementation

sys.modules[__name__] = _implementation
setattr(sys.modules[__package__], "internal_gateway", _implementation)

"""Compatibility facade for canonical identity defaults."""

from __future__ import annotations

import sys

try:
    from voidcube.domain.identity import defaults as _implementation
except ModuleNotFoundError:
    from src.voidcube.domain.identity import defaults as _implementation

sys.modules[__name__] = _implementation
setattr(sys.modules[__package__], "default_identity", _implementation)

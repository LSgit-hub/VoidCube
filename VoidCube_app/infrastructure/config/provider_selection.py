"""Compatibility module for canonical provider selection persistence."""

from __future__ import annotations

import sys

try:
    from voidcube.infrastructure.config import provider_selection as _implementation
except ModuleNotFoundError:
    from src.voidcube.infrastructure.config import provider_selection as _implementation

sys.modules[__name__] = _implementation
setattr(sys.modules[__package__], "provider_selection", _implementation)

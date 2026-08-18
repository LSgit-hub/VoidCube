"""Compatibility facade for canonical media generation provider routes."""

from __future__ import annotations

import sys

try:
    from voidcube.infrastructure.providers import media_generation as _implementation
except ModuleNotFoundError:
    from src.voidcube.infrastructure.providers import media_generation as _implementation

sys.modules[__name__] = _implementation
setattr(sys.modules[__package__], "media_generation_provider", _implementation)

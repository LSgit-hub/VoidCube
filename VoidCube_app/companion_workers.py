"""Compatibility facade for canonical companion worker routing."""

from __future__ import annotations

import sys

try:
    from voidcube.application import companion_workers as _implementation
except ModuleNotFoundError:
    from src.voidcube.application import companion_workers as _implementation

sys.modules[__name__] = _implementation
setattr(sys.modules[__package__], "companion_workers", _implementation)

"""Compatibility facade for canonical session application use cases."""

from __future__ import annotations

import sys

try:
    from voidcube.application import sessions as _implementation
except ModuleNotFoundError:
    from src.voidcube.application import sessions as _implementation

sys.modules[__name__] = _implementation
setattr(sys.modules[__package__], "sessions", _implementation)

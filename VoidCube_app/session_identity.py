"""Compatibility facade for canonical session identity rules."""

from __future__ import annotations

import sys

try:
    from voidcube.domain.session import identity as _implementation
except ModuleNotFoundError:
    from src.voidcube.domain.session import identity as _implementation

sys.modules[__name__] = _implementation
setattr(sys.modules[__package__], "session_identity", _implementation)

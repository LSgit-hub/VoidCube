"""Compatibility module for canonical session database infrastructure."""

from __future__ import annotations

import sys

try:
    from voidcube.infrastructure.persistence import session_db as _implementation
except ModuleNotFoundError:
    from src.voidcube.infrastructure.persistence import session_db as _implementation

sys.modules[__name__] = _implementation
setattr(sys.modules[__package__], "session_db", _implementation)

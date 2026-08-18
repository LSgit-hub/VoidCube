"""Compatibility module for infrastructure redaction."""

from __future__ import annotations

import sys

try:
    from voidcube.infrastructure.persistence import redaction as _implementation
except (ModuleNotFoundError, ImportError):
    from src.voidcube.infrastructure.persistence import redaction as _implementation

sys.modules[__name__] = _implementation
setattr(sys.modules[__package__], "redaction", _implementation)

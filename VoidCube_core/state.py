"""Compatibility module for the infrastructure session database."""

from __future__ import annotations

import sys

from VoidCube_app.infrastructure.persistence import session_db as _implementation

sys.modules[__name__] = _implementation
setattr(sys.modules[__package__], "state", _implementation)

"""Compatibility module for infrastructure redaction."""

from __future__ import annotations

import sys

from VoidCube_app.infrastructure.persistence import redaction as _implementation

sys.modules[__name__] = _implementation
setattr(sys.modules[__package__], "redaction", _implementation)

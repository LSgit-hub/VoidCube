"""Compatibility module for the canonical session lifecycle use case."""

from __future__ import annotations

import sys

from VoidCube_app.use_cases import sessions as _implementation

sys.modules[__name__] = _implementation
setattr(sys.modules[__package__], "session_lifecycle", _implementation)

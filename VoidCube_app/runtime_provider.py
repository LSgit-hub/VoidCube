"""Compatibility module for the canonical Provider runtime resolver."""

from __future__ import annotations

import sys

from VoidCube_app.infrastructure.providers import runtime as _implementation

sys.modules[__name__] = _implementation
setattr(sys.modules[__package__], "runtime_provider", _implementation)

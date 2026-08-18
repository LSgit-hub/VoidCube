"""Compatibility module for canonical runtime path services."""

from __future__ import annotations

import sys

try:
    from voidcube.infrastructure.config import runtime_paths as _implementation
except ModuleNotFoundError:
    from src.voidcube.infrastructure.config import runtime_paths as _implementation

sys.modules[__name__] = _implementation
setattr(sys.modules[__package__], "runtime_paths", _implementation)

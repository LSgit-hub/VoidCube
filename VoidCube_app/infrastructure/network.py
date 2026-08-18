"""Compatibility module for canonical network preferences."""

from __future__ import annotations

import sys

try:
    from voidcube.infrastructure import network as _implementation
except ModuleNotFoundError:
    from src.voidcube.infrastructure import network as _implementation

sys.modules[__name__] = _implementation
setattr(sys.modules[__package__], "network", _implementation)

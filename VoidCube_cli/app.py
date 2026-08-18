"""Compatibility module for the canonical CLI application host."""

from __future__ import annotations

import sys

try:
    from voidcube.interfaces.cli import application as _implementation
except ModuleNotFoundError:
    from src.voidcube.interfaces.cli import application as _implementation

sys.modules[__name__] = _implementation
setattr(sys.modules[__package__], "app", _implementation)

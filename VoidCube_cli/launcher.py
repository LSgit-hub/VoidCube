"""Compatibility module for the canonical ``voidcube.interfaces.cli`` launcher."""

from __future__ import annotations

import sys

try:
    from voidcube.interfaces.cli import launcher as _implementation
except ModuleNotFoundError:
    # Source-tree fallback before the src layout is installed.
    from src.voidcube.interfaces.cli import launcher as _implementation

sys.modules[__name__] = _implementation
setattr(sys.modules[__package__], "launcher", _implementation)

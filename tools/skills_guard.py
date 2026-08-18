"""Compatibility facade for canonical skill security scanning."""

from __future__ import annotations

import sys

try:
    from voidcube.extensions.skills import guard as _implementation
except ModuleNotFoundError:
    from src.voidcube.extensions.skills import guard as _implementation

sys.modules[__name__] = _implementation
setattr(sys.modules[__package__], "skills_guard", _implementation)

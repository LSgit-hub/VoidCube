"""Compatibility module for canonical skill catalog services."""

from __future__ import annotations

import sys

try:
    from voidcube.extensions.skills import catalog as _implementation
except ModuleNotFoundError:
    from src.voidcube.extensions.skills import catalog as _implementation

sys.modules[__name__] = _implementation
setattr(sys.modules[__package__], "skill_utils", _implementation)

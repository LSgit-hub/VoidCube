"""Compatibility facade for the canonical skill hub backend."""

from __future__ import annotations

import sys

try:
    from voidcube.extensions.skills import hub as _implementation
except ModuleNotFoundError:
    from src.voidcube.extensions.skills import hub as _implementation

sys.modules[__name__] = _implementation
setattr(sys.modules[__package__], "skills_hub", _implementation)

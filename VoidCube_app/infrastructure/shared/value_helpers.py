"""Compatibility module for canonical value helpers."""

from __future__ import annotations

import sys

try:
    from voidcube.infrastructure.shared import value_helpers as _implementation
except ModuleNotFoundError:
    from src.voidcube.infrastructure.shared import value_helpers as _implementation

sys.modules[__name__] = _implementation
setattr(sys.modules[__package__], "value_helpers", _implementation)

"""Compatibility facade for canonical supervisor autonomous executor."""

from __future__ import annotations

import sys

try:
    from voidcube.systems.supervisor import autonomous_executor as _implementation
except ModuleNotFoundError:
    from src.voidcube.systems.supervisor import autonomous_executor as _implementation

sys.modules[__name__] = _implementation
setattr(sys.modules[__package__], "autonomous_executor", _implementation)

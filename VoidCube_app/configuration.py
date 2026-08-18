"""Compatibility facade for canonical application configuration runtime."""

from __future__ import annotations

import sys

try:
    from voidcube.application import configuration as _implementation
except ModuleNotFoundError:
    from src.voidcube.application import configuration as _implementation

sys.modules[__name__] = _implementation
setattr(sys.modules[__package__], "configuration", _implementation)

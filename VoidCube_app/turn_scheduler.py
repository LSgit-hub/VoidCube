"""Compatibility facade for canonical application turn scheduling."""

from __future__ import annotations

import sys

try:
    from voidcube.application.scheduling import turn_scheduler as _implementation
except ModuleNotFoundError:
    from src.voidcube.application.scheduling import turn_scheduler as _implementation

sys.modules[__name__] = _implementation
setattr(sys.modules[__package__], "turn_scheduler", _implementation)

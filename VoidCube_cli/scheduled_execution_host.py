"""Compatibility facade for canonical scheduled execution host."""

from __future__ import annotations

import sys

try:
    from voidcube.application.scheduling import scheduled_execution_host as _implementation
except ModuleNotFoundError:
    from src.voidcube.application.scheduling import scheduled_execution_host as _implementation

sys.modules[__name__] = _implementation
setattr(sys.modules[__package__], "scheduled_execution_host", _implementation)

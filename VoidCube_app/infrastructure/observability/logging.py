"""Compatibility module for canonical observability services."""

from __future__ import annotations

import sys

try:
    from voidcube.infrastructure.observability import logging as _implementation
except ModuleNotFoundError:
    from src.voidcube.infrastructure.observability import logging as _implementation

sys.modules[__name__] = _implementation
setattr(sys.modules[__package__], "logging", _implementation)

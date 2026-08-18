"""Compatibility module for canonical provider registry."""

from __future__ import annotations

import sys

try:
    from voidcube.infrastructure.providers import registry as _implementation
except ModuleNotFoundError:
    from src.voidcube.infrastructure.providers import registry as _implementation

sys.modules[__name__] = _implementation
setattr(sys.modules[__package__], "registry", _implementation)

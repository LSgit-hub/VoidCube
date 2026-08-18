"""Compatibility facade for canonical integration policy contracts."""

from __future__ import annotations

import sys

try:
    from voidcube.domain.contracts import integration_policy as _implementation
except ModuleNotFoundError:
    from src.voidcube.domain.contracts import integration_policy as _implementation

sys.modules[__name__] = _implementation
setattr(sys.modules[__package__], "integration_policy", _implementation)

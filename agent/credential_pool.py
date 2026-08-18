"""Compatibility facade for the canonical provider credential pool."""

from __future__ import annotations

import sys

try:
    from voidcube.infrastructure.providers import credential_pool as _implementation
except ModuleNotFoundError:
    from src.voidcube.infrastructure.providers import credential_pool as _implementation

sys.modules[__name__] = _implementation
setattr(sys.modules[__package__], "credential_pool", _implementation)

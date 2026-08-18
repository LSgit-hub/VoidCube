"""Compatibility facade for canonical gateway service launcher."""

from __future__ import annotations

import sys

try:
    from voidcube.infrastructure.gateway import service_launcher as _implementation
except ModuleNotFoundError:
    from src.voidcube.infrastructure.gateway import service_launcher as _implementation

sys.modules[__name__] = _implementation
setattr(sys.modules[__package__], "serve", _implementation)

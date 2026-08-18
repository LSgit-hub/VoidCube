"""Compatibility facade for canonical gateway daemon lifecycle."""

from __future__ import annotations

import sys

try:
    from voidcube.infrastructure.gateway import daemon_runtime as _implementation
except ModuleNotFoundError:
    from src.voidcube.infrastructure.gateway import daemon_runtime as _implementation

sys.modules[__name__] = _implementation
setattr(sys.modules[__package__], "daemon_runtime", _implementation)

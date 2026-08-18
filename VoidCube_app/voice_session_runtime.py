"""Compatibility facade for canonical voice session runtime."""

from __future__ import annotations

import sys

try:
    from voidcube.interfaces.voice import session_runtime as _implementation
except ModuleNotFoundError:
    from src.voidcube.interfaces.voice import session_runtime as _implementation

sys.modules[__name__] = _implementation
setattr(sys.modules[__package__], "voice_session_runtime", _implementation)

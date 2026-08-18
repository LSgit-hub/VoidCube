"""Compatibility facade for canonical autonomous execution runtime."""

from __future__ import annotations

import sys

try:
    from voidcube.application.autonomous import execution_runtime as _implementation
except ModuleNotFoundError:
    from src.voidcube.application.autonomous import execution_runtime as _implementation

sys.modules[__name__] = _implementation
setattr(sys.modules[__package__], "autonomous_execution_runtime", _implementation)

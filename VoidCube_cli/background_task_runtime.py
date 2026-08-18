"""Compatibility facade for canonical background task runtime."""

from __future__ import annotations

import sys

try:
    from voidcube.application.scheduling import background_task_runtime as _implementation
except ModuleNotFoundError:
    from src.voidcube.application.scheduling import background_task_runtime as _implementation

sys.modules[__name__] = _implementation
setattr(sys.modules[__package__], "background_task_runtime", _implementation)
